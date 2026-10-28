# 운영용 GPU 서빙: Modal L40S 에 vLLM 으로 Gemma 31B(FP8)를 띄운다.
# AI 서버의 GPU_BASE_URL 이 이 앱의 주소다(app/core/config.py). extract·recommend-courses 가
# 먼저 여기로 오고, 실패하거나 시간을 넘기면 Gemini 로 넘어간다(app/models/factory.py).
#
# 구성(10/28 결정): compiled + GPU 메모리 스냅샷 + vLLM Sleep Mode. 10/27 측정에서 콜드 스타트
# 12.6~42.9초(중앙값 19.5), 웜 p50 4.87초. GPU 스냅샷은 Modal 알파 기능이라, 문제가 생기면
# KEEPGO_GPU_SNAPSHOT=0 으로 배포해 eager·스냅샷 없음(콜드 72~76초)으로 바꾼다. 주소는 같다.
#
# 필요한 Modal 리소스(한 번만 만들면 된다):
#   Volume keepgo-hf-cache     : 모델 가중치(HF_HOME). 받는 법은 ~/keepgo-bench/modal/download.py
#   Volume keepgo-vllm-cache   : torch.compile 결과
#   Secret keepgo-vllm-key     : VLLM_API_KEY — AI 서버의 GPU_API_KEY 와 같은 값
#
# modal 패키지는 운영 서버가 쓰지 않아 별도 의존성 그룹(modal)에 있다. 운영 이미지(--no-dev)에는 안 들어간다.
# 배포: uv run --group modal modal deploy infra/modal/gemma.py
# 내리기: uv run --group modal modal app stop keepgo-gemma31b
import os
import socket
import subprocess
import time
import urllib.request

import modal

MODEL = "RedHatAI/gemma-4-31B-it-FP8-dynamic"
REVISION = "d4ab4f579dd3516f97d8a6a4c98d0653480bad15"
PORT = 8000
SNAPSHOT = os.environ.get("KEEPGO_GPU_SNAPSHOT", "1") == "1"

image = (
    modal.Image.from_registry("nvidia/cuda:12.8.1-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.32.0", "huggingface_hub[hf_xet]")
    .env({
        "HF_HOME": "/cache/huggingface",
        "HF_HUB_OFFLINE": "1",
        "VLLM_SERVER_DEV_MODE": "1",          # /sleep, /wake_up 엔드포인트를 연다
        "TORCHINDUCTOR_COMPILE_THREADS": "1",  # 스냅샷 생성 실패 방지(Modal 문서)
    })
)
hf_volume = modal.Volume.from_name("keepgo-hf-cache")
vllm_volume = modal.Volume.from_name("keepgo-vllm-cache", create_if_missing=True)
app = modal.App("keepgo-gemma31b", image=image)

# 10/22 코랩 벤치와 같은 플래그. 8K 문맥에 L40S KV 캐시가 약 1.6~2개 분량이라
# 동시 요청은 8개로 막는다.
ARGS = [
    "vllm", "serve", MODEL,
    "--revision", REVISION,
    "--served-model-name", "gemma-31b",
    "--max-model-len", "8192",
    "--max-num-seqs", "8",
    "--max-num-batched-tokens", "2048",
    "--reasoning-parser", "gemma4",
    "--limit-mm-per-prompt", '{"image": 0, "video": 0}',
    "--host", "0.0.0.0",
    "--port", str(PORT),
]

COMMON = dict(
    gpu="L40S",
    volumes={"/cache": hf_volume, "/root/.cache/vllm": vllm_volume},
    secrets=[modal.Secret.from_name("keepgo-vllm-key")],
    # 마지막 요청 뒤 15분 유지(10/28 결정). 영상 분석(약 10분) 뒤 챗봇을 쓸 시간까지 덮는다.
    scaledown_window=15 * 60,
    # 비용 상한(시간당 약 $2). 넘치는 요청은 줄을 서다 AI 서버에서 Gemini 로 넘어간다.
    max_containers=1,
    timeout=60 * 60,
)


def _log(msg: str) -> None:
    print(f"[timing {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _wait_port(proc: subprocess.Popen, timeout: float = 20 * 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"vllm 이 종료됨 (코드 {proc.returncode})")
        try:
            socket.create_connection(("localhost", PORT), timeout=1).close()
            return
        except OSError:
            time.sleep(1)
    raise TimeoutError("vllm 포트가 열리지 않음")


def _post(path: str, body: bytes = b"", auth: bool = False, timeout: float = 600) -> int:
    req = urllib.request.Request(f"http://localhost:{PORT}{path}", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    if auth:
        req.add_header("Authorization", f"Bearer {os.environ['VLLM_API_KEY']}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def _warmup() -> None:
    # JIT 산출물을 스냅샷에 담으려고 짧은 생성 요청을 몇 번 보낸다(Modal 공식 예제와 같음).
    body = b'{"model": "gemma-31b", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 16}'
    for _ in range(3):
        _post("/v1/chat/completions", body, auth=True)


def _args() -> list[str]:
    return ARGS + ["--api-key", os.environ["VLLM_API_KEY"]]


if SNAPSHOT:

    @app.cls(
        **COMMON,
        memory=48 * 1024,  # sleep level 1 은 가중치(약 31GB)를 CPU 메모리로 옮긴다
        enable_memory_snapshot=True,
        experimental_options={"enable_gpu_snapshot": True},
    )
    @modal.concurrent(max_inputs=8)
    class Server:
        @modal.enter(snap=True)
        def start(self):
            _log("snap 단계 시작: vllm 띄움")
            self.proc = subprocess.Popen(_args() + ["--enable-sleep-mode"])
            _wait_port(self.proc)
            _log("포트 열림, 워밍업")
            _warmup()
            _log("워밍업 끝, sleep(level=1)")
            _post("/sleep?level=1")
            _log("sleep 끝 → 스냅샷 저장")

        @modal.enter(snap=False)
        def wake_up(self):
            _log("복원 후 wake_up 시작")
            _post("/wake_up")
            _wait_port(self.proc)
            _log("wake_up 끝")

        @modal.web_server(port=PORT, startup_timeout=20 * 60)
        def serve(self):
            pass

        @modal.exit()
        def stop(self):
            self.proc.terminate()

else:

    @app.cls(**COMMON)
    @modal.concurrent(max_inputs=8)
    class Server:
        @modal.enter()
        def start(self):
            _log("vllm 띄움(eager, 스냅샷 없음)")
            self.proc = subprocess.Popen(_args() + ["--enforce-eager"])
            _wait_port(self.proc)
            _log("포트 열림")

        @modal.web_server(port=PORT, startup_timeout=20 * 60)
        def serve(self):
            pass

        @modal.exit()
        def stop(self):
            self.proc.terminate()
