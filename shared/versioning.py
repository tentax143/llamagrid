AGENT_VERSION = "0.1.0"
HOST_VERSION = "0.1.0"
PROTOCOL_VERSION = 1
MIN_COMPATIBLE_AGENT = "0.1.0"
LLAMA_CPP_PINNED_TAG = "b4231"

# Download URLs — updated here only, referenced everywhere else
LLAMA_CPP_RELEASE_URL = (
    "https://github.com/ggerganov/llama.cpp/releases/download/"
    f"{LLAMA_CPP_PINNED_TAG}/"
    f"llama-{LLAMA_CPP_PINNED_TAG}-bin-win-cuda-cu12.2.0-x64.zip"
)
LLAMA_CPP_SHA256 = ""  # fill in after first fetch_binaries run

NSSM_URL = "https://nssm.cc/release/nssm-2.24.zip"
NSSM_SHA256 = ""

VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"

CUDA_VERSION_REQUIRED = "12.1"
DRIVER_VERSION_MIN = 535
