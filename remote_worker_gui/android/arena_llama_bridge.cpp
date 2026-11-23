// App-private bridge to the exact pinned llama.cpp ABI used by CI.
//
// This library exports no user-controlled HTTP, UI or scheduling interface.
// Only the Rust coordinator may call it after independently verifying the
// GGUF bytes and holding a counted native model-session guard. The bridge
// supports exactly one model at a time. A return code is NOT lease approval.
// Inference, token sampling and chess moves are deliberately not implemented.
#include "llama.h"
#include <mutex>
#include <cstring>

namespace {
std::mutex engine_mutex;
llama_model * loaded_model = nullptr;
bool backend_initialized = false;
}

extern "C" __attribute__((visibility("default")))
int arena_llama_model_open(const char * private_gguf_path) noexcept {
    if (private_gguf_path == nullptr || private_gguf_path[0] != '/') return -1;
    if (std::strlen(private_gguf_path) > 4096) return -1;
    std::lock_guard<std::mutex> guard(engine_mutex);
    if (loaded_model != nullptr) return -2;
    if (!backend_initialized) {
        llama_backend_init();
        backend_initialized = true;
    }
    llama_model_params params = llama_model_default_params();
    params.n_gpu_layers = 0;  // CPU-only until Android GPU backend is verified
    llama_model *model = llama_model_load_from_file(private_gguf_path, params);
    if (model == nullptr) return -3;
    loaded_model = model;
    return 0;
}

extern "C" __attribute__((visibility("default")))
void arena_llama_model_close() noexcept {
    std::lock_guard<std::mutex> guard(engine_mutex);
    if (loaded_model != nullptr) {
        llama_model_free(loaded_model);
        loaded_model = nullptr;
    }
}

extern "C" __attribute__((visibility("default")))
int arena_llama_model_is_open() noexcept {
    std::lock_guard<std::mutex> guard(engine_mutex);
    return loaded_model == nullptr ? 0 : 1;
}
