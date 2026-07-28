# syntax=docker/dockerfile:1
# Initialize device type args
# use build args in the docker build command with --build-arg="BUILDARG=true"
ARG USE_CUDA=false
ARG USE_OLLAMA=false
ARG USE_SLIM=false
ARG USE_EXTERNAL_SERVICES_SLIM=false
ARG USE_PERMISSION_HARDENING=false
# Tested with cu117 for CUDA 11 and cu121 for CUDA 12 (default)
ARG USE_CUDA_VER=cu128
# any sentence transformer model; models to use can be found at https://huggingface.co/models?library=sentence-transformers
# Leaderboard: https://huggingface.co/spaces/mteb/leaderboard 
# for better performance and multilangauge support use "intfloat/multilingual-e5-large" (~2.5GB) or "intfloat/multilingual-e5-base" (~1.5GB)
# IMPORTANT: If you change the embedding model (sentence-transformers/all-MiniLM-L6-v2) and vice versa, you aren't able to use RAG Chat with your previous documents loaded in the WebUI! You need to re-embed them.
ARG USE_EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
ARG USE_RERANKING_MODEL=""
ARG USE_AUXILIARY_EMBEDDING_MODEL=TaylorAI/bge-micro-v2

# Tiktoken encoding name; models to use can be found at https://huggingface.co/models?library=tiktoken
ARG USE_TIKTOKEN_ENCODING_NAME="cl100k_base"

ARG BUILD_HASH=dev-build
# Override at your own risk - non-root configurations are untested
ARG UID=0
ARG GID=0

######## WebUI frontend ########
FROM --platform=$BUILDPLATFORM node:22-alpine3.20 AS build
ARG BUILD_HASH
ARG ALPINE_MIRROR=
ARG NPM_REGISTRY=
ARG PYODIDE_CACHE_POLICY=prefer-local
ARG PYODIDE_INDEX_URL=
ARG PYODIDE_PYPI_API_BASE_URL=
ARG PYODIDE_PYPI_FILES_BASE_URL=
ARG PYODIDE_PYPI_INDEX_URLS=

# Set Node.js options (heap limit Allocation failed - JavaScript heap out of memory)
# ENV NODE_OPTIONS="--max-old-space-size=4096"

WORKDIR /app

# to store git revision in build
RUN set -e; \
    if [ -n "$ALPINE_MIRROR" ]; then \
    sed -i "s#https://dl-cdn.alpinelinux.org/alpine#$ALPINE_MIRROR#g" /etc/apk/repositories; \
    fi; \
    apk add --no-cache git

COPY package.json package-lock.json ./
COPY scripts/prepare-pyodide.js ./scripts/prepare-pyodide.js
COPY static/pyodide ./static/pyodide
# Cypress is only used for E2E tests; skip its binary download in production image builds.
ENV CYPRESS_INSTALL_BINARY=0
# onnxruntime-node otherwise tries to download CUDA providers during npm install on Linux/x64.
ENV ONNXRUNTIME_NODE_INSTALL_CUDA=skip \
    PYODIDE_CACHE_POLICY=${PYODIDE_CACHE_POLICY} \
    PYODIDE_INDEX_URL=${PYODIDE_INDEX_URL} \
    PYODIDE_PYPI_API_BASE_URL=${PYODIDE_PYPI_API_BASE_URL} \
    PYODIDE_PYPI_FILES_BASE_URL=${PYODIDE_PYPI_FILES_BASE_URL} \
    PYODIDE_PYPI_INDEX_URLS=${PYODIDE_PYPI_INDEX_URLS}
ENV NPM_CONFIG_FETCH_RETRIES=5 \
    NPM_CONFIG_FETCH_RETRY_MINTIMEOUT=20000 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000 \
    NPM_CONFIG_FETCH_TIMEOUT=600000
RUN --mount=type=cache,target=/root/.npm \
    set -e; \
    if [ -n "$NPM_REGISTRY" ]; then \
    npm config set registry "$NPM_REGISTRY"; \
    fi; \
    npm ci --force

# Keep Pyodide assets in a dependency-shaped layer so source-only changes do
# not force a full browser runtime download. The later `COPY . .` may contain
# a stale source checkout lock file, so preserve the generated assets outside
# the app tree and restore them before Vite copies `static/` into `build/`.
RUN mkdir -p static/pyodide && npm run pyodide:fetch && cp -a static/pyodide /tmp/pyodide-static

COPY . .
ENV APP_BUILD_HASH=${BUILD_HASH}
# Pyodide assets were already prefetched above into a cacheable dependency
# layer, so the final frontend build should not trigger a second fetch pass.
RUN rm -rf static/pyodide && mkdir -p static && cp -a /tmp/pyodide-static static/pyodide && ./node_modules/.bin/vite build

######## WebUI backend ########
FROM python:3.11-slim-bookworm AS base

# Use args
ARG USE_CUDA
ARG USE_OLLAMA
ARG USE_CUDA_VER
ARG USE_SLIM
ARG USE_EXTERNAL_SERVICES_SLIM
ARG USE_PERMISSION_HARDENING
ARG USE_EMBEDDING_MODEL
ARG USE_RERANKING_MODEL
ARG USE_AUXILIARY_EMBEDDING_MODEL
ARG UID
ARG GID
ARG APT_DEBIAN_MIRROR=
ARG APT_SECURITY_MIRROR=
ARG UV_DEFAULT_INDEX=

# Python settings
ENV PYTHONUNBUFFERED=1
ENV UV_HTTP_TIMEOUT=300

## Basis ##
ENV ENV=prod \
    PORT=8080 \
    # pass build args to the build
    USE_OLLAMA_DOCKER=${USE_OLLAMA} \
    USE_CUDA_DOCKER=${USE_CUDA} \
    USE_SLIM_DOCKER=${USE_SLIM} \
    USE_EXTERNAL_SERVICES_SLIM_DOCKER=${USE_EXTERNAL_SERVICES_SLIM} \
    USE_CUDA_DOCKER_VER=${USE_CUDA_VER} \
    USE_EMBEDDING_MODEL_DOCKER=${USE_EMBEDDING_MODEL} \
    USE_RERANKING_MODEL_DOCKER=${USE_RERANKING_MODEL} \
    USE_AUXILIARY_EMBEDDING_MODEL_DOCKER=${USE_AUXILIARY_EMBEDDING_MODEL}

## Basis URL Config ##
ENV OLLAMA_BASE_URL="/ollama" \
    OPENAI_API_BASE_URL=""

## API Key and Security Config ##
ENV OPENAI_API_KEY="" \
    WEBUI_SECRET_KEY="" \
    SCARF_NO_ANALYTICS=true \
    DO_NOT_TRACK=true \
    ANONYMIZED_TELEMETRY=false

#### Other models #########################################################
## whisper TTS model settings ##
ENV WHISPER_MODEL="base" \
    WHISPER_MODEL_DIR="/app/backend/data/cache/whisper/models"

## RAG Embedding model settings ##
ENV RAG_EMBEDDING_MODEL="$USE_EMBEDDING_MODEL_DOCKER" \
    RAG_RERANKING_MODEL="$USE_RERANKING_MODEL_DOCKER" \
    AUXILIARY_EMBEDDING_MODEL="$USE_AUXILIARY_EMBEDDING_MODEL_DOCKER" \
    SENTENCE_TRANSFORMERS_HOME="/app/backend/data/cache/embedding/models"

## Tiktoken model settings ##
ENV TIKTOKEN_ENCODING_NAME="cl100k_base" \
    TIKTOKEN_CACHE_DIR="/app/backend/data/cache/tiktoken"

## Hugging Face download cache ##
ENV HF_HOME="/app/backend/data/cache/embedding/models"

## NLTK corpus cache ##
ENV NLTK_DATA="/usr/local/share/nltk_data"

## Torch Extensions ##
# ENV TORCH_EXTENSIONS_DIR="/.cache/torch_extensions"

#### Other models ##########################################################

WORKDIR /app/backend

ENV HOME=/root
# Create user and group if not root
RUN if [ $UID -ne 0 ]; then \
    if [ $GID -ne 0 ]; then \
    addgroup --gid $GID app; \
    fi; \
    adduser --uid $UID --gid $GID --home $HOME --disabled-password --no-create-home app; \
    fi

RUN mkdir -p $HOME/.cache/chroma
RUN echo -n 00000000-0000-0000-0000-000000000000 > $HOME/.cache/chroma/telemetry_user_id

# Make sure the user has access to the app and root directory
RUN chown -R $UID:$GID /app $HOME

# Install system dependencies.
# The external-services slim profile intentionally skips local ML/OCR/browser
# build deps and the OpenCV runtime libs they require.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    set -e; \
    rm -f /etc/apt/apt.conf.d/docker-clean; \
    if [ "$USE_EXTERNAL_SERVICES_SLIM" = "true" ] && [ "$USE_CUDA" = "true" ]; then \
    echo "USE_EXTERNAL_SERVICES_SLIM=true is incompatible with USE_CUDA=true" >&2; \
    exit 1; \
    fi; \
    if [ -n "$APT_DEBIAN_MIRROR" ]; then \
    sed -ri "s#https?://deb.debian.org/debian#$APT_DEBIAN_MIRROR#g" /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi; \
    if [ -n "$APT_SECURITY_MIRROR" ]; then \
    sed -ri "s#https?://deb.debian.org/debian-security#$APT_SECURITY_MIRROR#g; s#https?://security.debian.org/debian-security#$APT_SECURITY_MIRROR#g" /etc/apt/sources.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
    fi; \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 update && \
    if [ "$USE_EXTERNAL_SERVICES_SLIM" = "true" ]; then \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 install -y --no-install-recommends \
    git pandoc netcat-openbsd curl jq ffmpeg zstd; \
    else \
    apt-get -o Acquire::Retries=5 -o Acquire::http::Timeout=120 install -y --no-install-recommends \
    git build-essential pandoc gcc netcat-openbsd curl jq ca-certificates \
    libmariadb-dev \
    python3-dev \
    ffmpeg libsm6 libxext6 zstd; \
    fi

# install python dependencies
COPY --chown=$UID:$GID ./backend/requirements.txt ./requirements.txt
COPY --chown=$UID:$GID ./backend/requirements-external-slim.txt ./requirements-external-slim.txt

# Set UV_LINK_MODE to copy to prevent 0-byte file corruption in QEMU arm64 cross-builds
ENV UV_LINK_MODE=copy

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=cache,target=/root/.cache/uv \
    set -e; \
    REQUIREMENTS_FILE="requirements.txt"; \
    if [ "$USE_EXTERNAL_SERVICES_SLIM" = "true" ]; then \
    REQUIREMENTS_FILE="requirements-external-slim.txt"; \
    fi; \
    UV_INDEX_ARGS=""; \
    PIP_INDEX_ARGS=""; \
    if [ -n "$UV_DEFAULT_INDEX" ]; then \
    UV_INDEX_ARGS="--default-index $UV_DEFAULT_INDEX"; \
    PIP_INDEX_ARGS="-i $UV_DEFAULT_INDEX"; \
    fi; \
    pip3 install $PIP_INDEX_ARGS uv; \
    if [ "$USE_EXTERNAL_SERVICES_SLIM" = "true" ]; then \
    uv pip install --system -r "$REQUIREMENTS_FILE" $UV_INDEX_ARGS; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    elif [ "$USE_CUDA" = "true" ]; then \
    # If you use CUDA the whisper and embedding model will be downloaded on first use
    # fix: pin torch<=2.9.1 - torch 2.10.0 aarch64 wheels cause SIGILL on ARM devices (RPi 4 Cortex-A72) #21349
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url https://download.pytorch.org/whl/$USE_CUDA_DOCKER_VER; \
    uv pip install --system -r "$REQUIREMENTS_FILE" $UV_INDEX_ARGS; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')"; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    else \
    pip3 install 'torch<=2.9.1' torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu; \
    uv pip install --system -r "$REQUIREMENTS_FILE" $UV_INDEX_ARGS; \
    if [ "$USE_SLIM" != "true" ]; then \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ['RAG_EMBEDDING_MODEL'], device='cpu')"; \
    python -c "import os; from sentence_transformers import SentenceTransformer; SentenceTransformer(os.environ.get('AUXILIARY_EMBEDDING_MODEL', 'TaylorAI/bge-micro-v2'), device='cpu')"; \
    python -c "import os; from faster_whisper import WhisperModel; WhisperModel(os.environ['WHISPER_MODEL'], device='cpu', compute_type='int8', download_root=os.environ['WHISPER_MODEL_DIR'])"; \
    python -c "import os; import tiktoken; tiktoken.get_encoding(os.environ['TIKTOKEN_ENCODING_NAME'])"; \
    fi; \
    fi; \
    mkdir -p /app/backend/data; chown -R $UID:$GID /app/backend/data/; \
    rm -rf /var/lib/apt/lists/*;

# Keep NLTK data in its own cacheable layer. This makes a corpus download
# failure explicit and avoids hiding it behind runtime startup retries.
RUN set -e; \
    if [ "$USE_EXTERNAL_SERVICES_SLIM" = "true" ] || [ "$USE_CUDA" = "true" ] || [ "$USE_SLIM" != "true" ]; then \
    mkdir -p "$NLTK_DATA"; \
    python -c "import os; import nltk; nltk.download('punkt_tab', download_dir=os.environ['NLTK_DATA'], raise_on_error=True)"; \
    fi

# Install Ollama if requested
RUN if [ "$USE_OLLAMA" = "true" ]; then \
    date +%s > /tmp/ollama_build_hash && \
    echo "Cache broken at timestamp: `cat /tmp/ollama_build_hash`" && \
    curl -fsSL https://ollama.com/install.sh | sh && \
    rm -rf /var/lib/apt/lists/*; \
    fi

# copy embedding weight from build
# RUN mkdir -p /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2
# COPY --from=build /app/onnx /root/.cache/chroma/onnx_models/all-MiniLM-L6-v2/onnx

# copy built frontend files
COPY --chown=$UID:$GID --from=build /app/build /app/build
COPY --chown=$UID:$GID --from=build /app/CHANGELOG.md /app/CHANGELOG.md
COPY --chown=$UID:$GID --from=build /app/package.json /app/package.json

# copy backend files
COPY --chown=$UID:$GID ./backend .

# The backend rewrites its bundled static assets (favicons, splash, manifest,
# loader.js, ...) under open_webui/static at startup. Make that directory
# writable by an arbitrary UID -- which under OpenShift's restricted SCC is
# always a member of GID 0 -- so those writes don't fail with EACCES and crash
# the boot log with "[Errno 13] Permission denied". `chmod -R g=u` mirrors the
# owner bits onto the group (the Red Hat arbitrary-UID idiom). This is applied
# unconditionally because it targets a directory the app writes on every start;
# the broader, opt-in USE_PERMISSION_HARDENING below covers the rest of /app.
RUN chgrp -R 0 /app/backend/open_webui/static && \
    chmod -R g=u /app/backend/open_webui/static

EXPOSE 8080

HEALTHCHECK CMD curl --silent --fail http://localhost:${PORT:-8080}/health | jq -ne 'input.status == true' || exit 1

# Minimal, atomic permission hardening for OpenShift (arbitrary UID):
# - Group 0 owns /app and /root
# - Directories are group-writable and have SGID so new files inherit GID 0
RUN if [ "$USE_PERMISSION_HARDENING" = "true" ]; then \
    set -eux; \
    chgrp -R 0 /app /root || true; \
    chmod -R g+rwX /app /root || true; \
    find /app -type d -exec chmod g+s {} + || true; \
    find /root -type d -exec chmod g+s {} + || true; \
    fi

USER $UID:$GID

ARG BUILD_HASH
ENV WEBUI_BUILD_VERSION=${BUILD_HASH}
ENV DOCKER=true

CMD [ "bash", "start.sh"]
