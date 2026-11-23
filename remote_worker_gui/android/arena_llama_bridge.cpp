// App-private bridge to the exact pinned llama.cpp ABI used by CI.
//
// This library exports no user-controlled HTTP, UI or scheduling interface.
// Only the Rust coordinator may call it after independently verifying the
// GGUF bytes and holding a counted native model-session guard. The bridge
// supports exactly one model at a time. A return code is NOT lease approval.
// Inference, token sampling and chess moves are deliberately not implemented.
#include "llama.h"
#include <mutex>
#include <atomic>
#include <cstring>
#include <vector>
#include <string>
#include <algorithm>

namespace {
std::mutex engine_mutex;
llama_model * loaded_model = nullptr;
bool backend_initialized = false;
// STOP may be raised while generation holds engine_mutex. It remains sticky
// until the old model is destroyed; a new invocation must not clear it.
std::atomic<bool> stop_requested{false};
std::atomic<bool> inference_active{false};
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
    stop_requested.store(false, std::memory_order_release);
    return 0;
}

extern "C" __attribute__((visibility("default")))
void arena_llama_model_close() noexcept {
    // Cancel first; mutex acquisition waits for in-flight generation to exit.
    stop_requested.store(true, std::memory_order_release);
    std::lock_guard<std::mutex> guard(engine_mutex);
    if (loaded_model != nullptr) {
        llama_model_free(loaded_model);
        loaded_model = nullptr;
    }
}

extern "C" __attribute__((visibility("default")))
int arena_llama_request_stop() noexcept {
    stop_requested.store(true, std::memory_order_release);
    // This is an instruction, NOT a verified STOP/ACK.
    return 0;
}

extern "C" __attribute__((visibility("default")))
int arena_llama_inference_quiesced() noexcept {
    // No generation may begin after a sticky STOP on the current model.
    // This local signal alone is insufficient for coordinator ACK.
    return stop_requested.load(std::memory_order_acquire) &&
           !inference_active.load(std::memory_order_acquire) ? 1 : 0;
}

extern "C" __attribute__((visibility("default")))
int arena_llama_model_is_open() noexcept {
    std::lock_guard<std::mutex> guard(engine_mutex);
    return loaded_model == nullptr ? 0 : 1;
}


// Bounded CPU-only experimental text generation. This routine is internal:
// neither the JS webview nor the remote-worker dispatch can call it yet.
// Returns negative on model/ABI/tokenization/decode errors; output buffer is
// always NUL-terminated on success. Caller validates exact UCI + legal list.
extern "C" __attribute__((visibility("default")))
int arena_llama_generate_greedy(
    const char * prompt, int prompt_len, int max_tokens,
    char * output, int output_capacity
) noexcept {
    if (prompt == nullptr || output == nullptr ||
        prompt_len < 1 || prompt_len > 8192 ||
        max_tokens < 1 || max_tokens > 64 ||
        output_capacity < 2 || output_capacity > 4096) return -1;
    std::lock_guard<std::mutex> guard(engine_mutex);
    if (loaded_model == nullptr) return -2;
    inference_active.store(true, std::memory_order_release);
    struct ActivityCleanup {
        ~ActivityCleanup() { inference_active.store(false, std::memory_order_release); }
    } activity_cleanup;
    if (stop_requested.load(std::memory_order_acquire)) return -11;
    output[0] = '\0';

    llama_context_params params = llama_context_default_params();
    params.n_ctx = 2048;
    params.n_batch = 1024;
    params.n_ubatch = 1024;
    params.n_threads = 2;
    params.n_threads_batch = 2;
    params.abort_callback = [](void *) {
        return stop_requested.load(std::memory_order_acquire);
    };
    llama_context * ctx = llama_init_from_model(loaded_model, params);
    if (ctx == nullptr) return -3;
    struct ContextCleanup {
        llama_context * ptr;
        ~ContextCleanup() { llama_free(ptr); }
    } context_cleanup{ctx};
    const llama_vocab * vocab = llama_model_get_vocab(loaded_model);
    if (vocab == nullptr) return -4;
    std::vector<llama_token> tokens(1024);
    int n = llama_tokenize(vocab, prompt, prompt_len,
                           tokens.data(), static_cast<int>(tokens.size()),
                           true, false);
    if (n < 1 || n > 1024 || n + max_tokens >= 2048) return -5;
    tokens.resize(static_cast<size_t>(n));
    if (stop_requested.load(std::memory_order_acquire)) return -11;
    if (llama_decode(ctx, llama_batch_get_one(tokens.data(), n)) != 0) return -6;
    if (stop_requested.load(std::memory_order_acquire)) return -11;

    llama_sampler * sampler = llama_sampler_init_greedy();
    if (sampler == nullptr) return -7;
    struct SamplerCleanup {
        llama_sampler * ptr;
        ~SamplerCleanup() { llama_sampler_free(ptr); }
    } sampler_cleanup{sampler};

    int used = 0;
    for (int step = 0; step < max_tokens; step++) {
        if (stop_requested.load(std::memory_order_acquire)) return -11;
        llama_token next = llama_sampler_sample(sampler, ctx, -1);
        if (llama_vocab_is_eog(vocab, next)) break;
        char piece[256];
        int bytes = llama_token_to_piece(vocab, next, piece,
                                        static_cast<int>(sizeof(piece)), 0, false);
        if (bytes < 0 || bytes > static_cast<int>(sizeof(piece))) return -8;
        if (used + bytes >= output_capacity) return -9;
        bool newline = false;
        for (int i = 0; i < bytes; i++) {
            if (piece[i] == '\n' || piece[i] == '\r') {
                newline = true;
                break;
            }
            output[used++] = piece[i];
        }
        output[used] = '\0';
        if (newline) break;
        if (stop_requested.load(std::memory_order_acquire)) return -11;
        if (llama_decode(ctx, llama_batch_get_one(&next, 1)) != 0) return -10;
    }
    if (stop_requested.load(std::memory_order_acquire)) return -11;
    return used;
}
