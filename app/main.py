from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pathlib import Path
from datetime import datetime
from typing import List
import os
import uuid

import cv2
import numpy as np
import boto3

from insightface.app import FaceAnalysis

app = FastAPI(title="Face Detection & Embedding API (InsightFace)")

# --------------------------------------------------------------------
# Configurações AWS
# --------------------------------------------------------------------
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_KEY")
AWS_PUBLIC_BUCKET_NAME = os.getenv("AWS_PUBLIC_BUCKET_NAME")

if not AWS_PUBLIC_BUCKET_NAME:
    raise RuntimeError("AWS_PUBLIC_BUCKET_NAME não configurado.")

if not AWS_ACCESS_KEY or not AWS_SECRET_KEY:
    raise RuntimeError(
        "Credenciais AWS (AWS_ACCESS_KEY / AWS_SECRET_KEY) não configuradas."
    )

s3_client = boto3.client(
    "s3",
    region_name=AWS_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

PUBLIC_BASE_URL = f"https://{AWS_PUBLIC_BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com"

OUTPUT_DIR = Path("faces")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# --------------------------------------------------------------------
# InsightFace: detecção + embedding
# --------------------------------------------------------------------
INSIGHTFACE_MODEL = os.getenv("INSIGHTFACE_MODEL", "buffalo_l")
INSIGHTFACE_CTX_ID = int(os.getenv("INSIGHTFACE_CTX_ID", "-1"))  # -1 = CPU, 0 = GPU

face_app = FaceAnalysis(name=INSIGHTFACE_MODEL)
# det_size pode ser ajustado conforme tamanho típico de entrada
face_app.prepare(ctx_id=INSIGHTFACE_CTX_ID, det_size=(640, 640))


# --------------------------------------------------------------------
# Funções auxiliares
# --------------------------------------------------------------------
def read_image_from_bytes(data: bytes) -> np.ndarray:
    np_arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Não foi possível decodificar a imagem.")
    return img


def upload_crop_to_s3(image: np.ndarray, folder: str = "faces") -> str:
    """
    Codifica o crop em JPEG na memória e faz upload para o S3.
    Retorna a URL pública do arquivo.
    """
    success, buffer = cv2.imencode(".jpg", image)
    if not success:
        raise ValueError("Erro ao codificar o crop em JPEG.")

    file_bytes = buffer.tobytes()

    timestamp = int(datetime.utcnow().timestamp() * 1000)
    random_suffix = uuid.uuid4().hex[:8]
    file_name = f"face_{timestamp}_{random_suffix}.jpg"

    safe_folder = folder.strip("/")

    if safe_folder:
        key = f"{safe_folder}/{timestamp}-{file_name}"
    else:
        key = f"{timestamp}-{file_name}"

    try:
        s3_client.put_object(
            Bucket=AWS_PUBLIC_BUCKET_NAME,
            Key=key,
            Body=file_bytes,
            ContentType="image/jpeg",
            ACL="public-read",
        )
    except Exception as e:
        raise RuntimeError(f"Falha ao fazer upload para S3: {str(e)}") from e

    url = f"{PUBLIC_BASE_URL}/{key}"
    return url


# --------------------------------------------------------------------
# Endpoint: exclusivamente rostos + embeddings (InsightFace)
# --------------------------------------------------------------------
@app.post("/detect-faces")
async def detect_faces(file: UploadFile = File(...)):
    """
    Endpoint que:
      1) Recebe uma imagem
      2) Detecta todos os ROSTOS usando InsightFace
      3) Faz crop de cada rosto a partir do bbox
      4) Sobe o crop pro S3
      5) Retorna: face_id, bbox, entity_path (URL) e embedding L2-normalizado.
         (embedding vem de face.normed_embedding, pronto para similaridade de cosseno)
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=400,
            detail="Tipo de arquivo inválido. Envie uma imagem (image/*).",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    try:
        img = read_image_from_bytes(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # InsightFace espera imagem em BGR (cv2 padrão está ok)
    faces = face_app.get(img)

    if len(faces) == 0:
        return JSONResponse(
            status_code=200,
            content={
                "message": "Nenhum rosto detectado na imagem.",
                "faces_count": 0,
                "faces": [],
            },
        )

    faces_response = []

    try:
        for idx, face in enumerate(faces):
            # bbox = [x1, y1, x2, y2] em float -> converte p/ int
            x1, y1, x2, y2 = face.bbox.astype(int)

            # Garante que está dentro do shape da imagem
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(img.shape[1], x2)
            y2 = min(img.shape[0], y2)

            face_crop = img[y1:y2, x1:x2]
            if face_crop is None or face_crop.size == 0:
                continue

            # URL pública no S3 (crop do rosto)
            url = upload_crop_to_s3(face_crop, folder="faces")

            # InsightFace já retorna embedding normalizado:
            # - face.embedding  -> sem normalizar
            # - face.normed_embedding -> L2-normalizado (ideal para cosseno)
            embedding_np = face.normed_embedding.astype(np.float32)
            embedding: List[float] = embedding_np.astype(float).tolist()

            faces_response.append(
                {
                    "face_id": idx,
                    "bbox": {
                        "x1": int(x1),
                        "y1": int(y1),
                        "x2": int(x2),
                        "y2": int(y2),
                    },
                    "entity_path": url,
                    "embedding": embedding,
                }
            )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao salvar rostos no S3 ou gerar embeddings: {str(e)}",
        )

    response_data = {
        "message": "Rostos detectados e embeddings gerados com InsightFace.",
        "faces_count": len(faces_response),
        "faces": faces_response,
    }

    return JSONResponse(status_code=200, content=response_data)
