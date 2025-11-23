{
  description = "Dev environment (Nix flake) for FastAPI + Poetry";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils, ... }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = import nixpkgs { inherit system; };
        python = pkgs.python312;

        pythonEnv = python.withPackages (ps: with ps; [ psycopg2-binary ]);
      in {
        devShells.default = pkgs.mkShell {
          name = "fastapi-dev-shell";

          buildInputs = [
            pythonEnv
            pkgs.python312Packages.numpy
            pkgs.python312Packages.opencv-python-headless
            pkgs.poetry
            pkgs.pyright
            pkgs.pre-commit
            pkgs.commitizen
            pkgs.postgresql
            pkgs.git
            pkgs.docker-compose
            pkgs.ruff
            pkgs.gettext
          ];

          shellHook = ''
            export PIP_ROOT_USER_ACTION=ignore

            if [ ! -d .git ]; then
              git init
              echo "📁  Repositório Git inicializado."
            fi

            if ! command -v poetry >/dev/null 2>&1; then
              echo "⚠️  Poetry não encontrado no PATH do shell."
            fi

            if [ -f pyproject.toml ] && [ ! -d .venv ]; then
              echo "📦  Instalando dependências via Poetry..."
              poetry install --no-interaction --no-ansi || true
            fi

            if [ -d .venv ]; then
              echo "🐍  Ativando virtualenv (.venv)..."
              source .venv/bin/activate
            else
              echo "ℹ️  Use 'poetry install' para criar .venv e instalar deps."
            fi

            pre-commit install --install-hooks --hook-type pre-commit >/dev/null || true
            pre-commit install --install-hooks --hook-type commit-msg >/dev/null || true
            echo "✅  Hooks pre-commit + commit-msg instalados (Commitizen incluído)."

            exec "$(getent passwd "$USER" | cut -d: -f7)" -l
          '';
        };

        formatter = pkgs.alejandra;
      });
}
