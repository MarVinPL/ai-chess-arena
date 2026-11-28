package pl.marvin.aichessarena.worker

import android.content.Context
import android.net.http.HttpEngine
import android.net.http.HttpException
import android.net.http.UrlRequest
import android.net.http.UrlResponseInfo
import android.os.Build
import java.io.File
import java.io.FileOutputStream
import java.net.URI
import java.nio.ByteBuffer
import java.security.MessageDigest
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Android 14+ OS networking stack (built into Android 16).
 *
 * The platform HttpEngine negotiates QUIC/HTTP3 when supported and otherwise
 * HTTPS/HTTP2. No Google Play Services or third-party paid transport.
 *
 * This bridge is NOT reachable from WebView. Only the trusted Rust Arena
 * model-registry path should invoke it; it validates URLs and bytes again.
 * Experimental transport remains dormant until verified registry assignment,
 * STOP/ACK and post-download GGUF verification are fully integrated.
 * One transfer, no persistent HTTP body cache, exactly one app-private GGUF.
 */
object WorkerQuicTransport {
    private const val MAX_BYTES = 16L * 1024 * 1024 * 1024
    private val busy = AtomicBoolean(false)
    // Registered by Rust before transfer; no capability is exposed to WebView.
    @JvmStatic
    private external fun nativePreparationAllowed(): Boolean

    private fun requirePreparation() {
        check(nativePreparationAllowed()) { "GGUF preparation lease revoked" }
    }

    private val hex = Regex("^[0-9a-f]{64}$")
    private val pinned = Regex(
        "^/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/resolve/[0-9a-fA-F]{40}/[A-Za-z0-9_.-]+\\.gguf$"
    )
    private val redirectHosts = setOf(
        "huggingface.co", "cdn-lfs.hf.co",
        "cas-bridge.xethub.hf.co", "transfer.xethub.hf.co"
    )

    private fun canonicalUri(raw: String): URI {
        val uri = URI(raw)
        require(uri.scheme == "https" && uri.port == -1 && uri.rawUserInfo == null)
        require(uri.rawFragment == null)
        require(uri.host in redirectHosts && !uri.rawPath.contains("%"))
        return uri
    }

    private fun approvedUri(raw: String): URI {
        val uri = canonicalUri(raw)
        require(uri.host == "huggingface.co" && uri.rawQuery == null)
        require(pinned.matches(uri.rawPath))
        return uri
    }

    /**
     * No GGUF download. Fixed HTTPS HEAD probe for a network diagnostic only.
     * A fresh QUIC connection may negotiate HTTP/2 until server Alt-Svc is known.
     * The result is NOT evidence of model availability or Arena authentication.
     */
    @JvmStatic
    fun probeProtocol(context: Context): String {
        require(Build.VERSION.SDK_INT >= 34)
        val target = "https://huggingface.co/robots.txt"
        val executor = Executors.newSingleThreadExecutor()
        var engine: HttpEngine? = null
        var request: UrlRequest? = null
        val done = CountDownLatch(1)
        var result: String? = null
        var failure: String? = null
        try {
            engine = HttpEngine.Builder(context)
                .setEnableQuic(true)
                .setEnableHttp2(true)
                .build()
            val callback = object : UrlRequest.Callback {
                override fun onRedirectReceived(
                    request: UrlRequest, info: UrlResponseInfo, newLocationUrl: String
                ) {
                    failure = "Redirect in QUIC probe"
                    request.cancel()
                }
                override fun onResponseStarted(request: UrlRequest, info: UrlResponseInfo) {
                    if (info.httpStatusCode !in 200..299) {
                        failure = "QUIC probe returned HTTP ${info.httpStatusCode}"
                    } else {
                        result = info.negotiatedProtocol
                    }
                    // HEAD has no response body; complete the callback lifecycle.
                    request.cancel()
                }
                override fun onReadCompleted(
                    request: UrlRequest, info: UrlResponseInfo, byteBuffer: ByteBuffer
                ) {
                    failure = "Unexpected body in HEAD response"
                    request.cancel()
                }
                override fun onSucceeded(request: UrlRequest, info: UrlResponseInfo) {
                    done.countDown()
                }
                override fun onFailed(
                    request: UrlRequest, info: UrlResponseInfo?, exception: HttpException
                ) {
                    failure = "QUIC network probe failed"
                    done.countDown()
                }
                override fun onCanceled(request: UrlRequest, info: UrlResponseInfo?) {
                    done.countDown()
                }
            }
            request = engine.newUrlRequestBuilder(target, executor, callback)
                .setHttpMethod("HEAD")
                .build()
            request.start()
            check(done.await(20, TimeUnit.SECONDS)) { "QUIC probe timeout" }
            check(failure == null && result != null) { failure ?: "QUIC probe failed" }
            return result
        } finally {
            try { request?.cancel() } catch (_: Exception) {}
            try { engine?.shutdown() } catch (_: Exception) {}
            executor.shutdownNow()
        }
    }

    /**
     * Only callable from a trusted worker thread; synchronous JNI boundary
     * awaits the asynchronous platform callback. Returns negotiated protocol.
     * Rust must still rehash the final file and require an active registry
     * approval before marking any model as READY.
     */
    @JvmStatic
    fun downloadApproved(
        context: Context, sourceUrl: String, sha256: String,
        expectedBytes: Long, reserveBytes: Long
    ): String {
        require(Build.VERSION.SDK_INT >= 34)
        require(hex.matches(sha256))
        require(expectedBytes in 4..MAX_BYTES && reserveBytes >= 0)
        approvedUri(sourceUrl)
        requirePreparation()
        check(busy.compareAndSet(false, true)) { "GGUF transfer already active" }
        var partial: File? = null
        var request: UrlRequest? = null
        var engine: HttpEngine? = null
        val executor = Executors.newSingleThreadExecutor()
        // A timed-out transfer must never be promoted by a late callback.
        val aborted = AtomicBoolean(false)
        try {
            val root = File(context.filesDir, "worker-gguf-models")
            check((root.isDirectory || root.mkdir()) && !java.nio.file.Files.isSymbolicLink(root.toPath()))
            val complete = File(root, "$sha256.gguf")
            check(!java.nio.file.Files.exists(complete.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS)) { "GGUF target exists; validate/reconcile through Rust" }
            val pending = File(root, "$sha256.gguf.partial")
            check(pending.createNewFile()) { "GGUF partial exists; require trusted reconciliation" }
            partial = pending
            check(root.usableSpace >= Math.addExact(expectedBytes, reserveBytes)) {
                "insufficient_storage"
            }

            engine = HttpEngine.Builder(context)
                .setEnableQuic(true)
                .setEnableHttp2(true)
                .build()

            val done = CountDownLatch(1)
            var error: String? = null
            var protocol = ""
            var received = 0L
            var headerBytes = 0
            val header = ByteArray(4)
            var redirects = 0
            val sha = MessageDigest.getInstance("SHA-256")
            val stream = FileOutputStream(pending, false)
            val buffer = ByteBuffer.allocateDirect(64 * 1024)
            val callback = object : UrlRequest.Callback {
                override fun onRedirectReceived(
                    request: UrlRequest, info: UrlResponseInfo, newLocationUrl: String
                ) {
                    try {
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        check(redirects++ < 3) { "Too many redirects" }
                        canonicalUri(newLocationUrl)
                        request.followRedirect()
                    } catch (e: Exception) {
                        error = "Untrusted GGUF redirect"
                        request.cancel()
                    }
                }

                override fun onResponseStarted(request: UrlRequest, info: UrlResponseInfo) {
                    try {
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        check(info.httpStatusCode == 200) { "Unexpected GGUF status" }
                        val reportedLength = info.headers.asMap.entries
                            .firstOrNull { it.key.equals("content-length", ignoreCase = true) }
                            ?.value?.firstOrNull()?.toLongOrNull()
                        if (reportedLength != null) {
                            check(reportedLength == expectedBytes) { "GGUF length mismatch" }
                        }
                        canonicalUri(info.url)
                        protocol = info.negotiatedProtocol
                        buffer.clear()
                        request.read(buffer)
                    } catch (e: Exception) {
                        error = "Invalid GGUF response"
                        request.cancel()
                    }
                }

                override fun onReadCompleted(
                    request: UrlRequest, info: UrlResponseInfo, byteBuffer: ByteBuffer
                ) {
                    try {
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        byteBuffer.flip()
                        val count = byteBuffer.remaining()
                        received = Math.addExact(received, count.toLong())
                        check(received <= expectedBytes) { "GGUF exceeds approved size" }
                        check(root.usableSpace >=
                            Math.addExact(expectedBytes - received + count.toLong(), reserveBytes)) {
                            "insufficient_storage"
                        }
                        val bytes = ByteArray(count)
                        byteBuffer.get(bytes)
                        if (headerBytes < header.size) {
                            val take = minOf(bytes.size, header.size - headerBytes)
                            bytes.copyInto(header, headerBytes, 0, take)
                            headerBytes += take
                            if (headerBytes == header.size) {
                                check(header.contentEquals(byteArrayOf(71, 71, 85, 70))) {
                                    "GGUF header mismatch"
                                }
                            }
                        }
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        stream.write(bytes)
                        sha.update(bytes)
                        byteBuffer.clear()
                        request.read(byteBuffer)
                    } catch (e: Exception) {
                        error = "GGUF storage, length or stream failure"
                        request.cancel()
                    }
                }

                override fun onSucceeded(request: UrlRequest, info: UrlResponseInfo) {
                    try {
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        stream.fd.sync()
                        check(headerBytes == header.size &&
                            header.contentEquals(byteArrayOf(71, 71, 85, 70))) {
                            "GGUF header mismatch"
                        }
                        check(received == expectedBytes) { "Truncated GGUF" }
                        val actual = sha.digest().joinToString("") { "%02x".format(it) }
                        check(actual == sha256) { "GGUF SHA-256 mismatch" }
                        check(root.usableSpace >= reserveBytes) { "insufficient_storage" }
                        check(!java.nio.file.Files.exists(complete.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS)) { "GGUF target already exists" }
                        check(!aborted.get()) { "GGUF transfer aborted" }
                        requirePreparation()
                        check(protocol in setOf("h3", "h2", "http/1.1")) { "Unknown transport" }
                    } catch (e: Exception) {
                        error = "GGUF integrity or publishing failed"
                    } finally { done.countDown() }
                }

                override fun onFailed(request: UrlRequest, info: UrlResponseInfo?,
                    exception: HttpException) {
                    error = "GGUF network transfer failed"
                    done.countDown()
                }
                override fun onCanceled(request: UrlRequest, info: UrlResponseInfo?) {
                    if (error == null) error = "GGUF transfer canceled"
                    done.countDown()
                }
            }
            try {
                request = engine.newUrlRequestBuilder(sourceUrl, executor, callback).build()
                request.start()
                val deadline = System.nanoTime() + TimeUnit.HOURS.toNanos(2)
                while (!done.await(1, TimeUnit.SECONDS)) {
                    requirePreparation()
                    check(System.nanoTime() < deadline) { "GGUF transfer timed out" }
                }
            } finally {
                // Abort publication before asynchronous cancellation returns.
                aborted.set(true)
                request?.cancel()
                executor.shutdown()
                // If a callback is still running, retain the busy fence until it exits.
                while (!executor.awaitTermination(1, TimeUnit.SECONDS)) { }
                stream.close()
            }
            check(error == null) { error ?: "GGUF transfer failed" }
            check(root.usableSpace >= reserveBytes) { "insufficient_storage" }
            check(!java.nio.file.Files.exists(complete.toPath(), java.nio.file.LinkOption.NOFOLLOW_LINKS))
            requirePreparation()
            java.nio.file.Files.move(pending.toPath(), complete.toPath(), java.nio.file.StandardCopyOption.ATOMIC_MOVE)
            return protocol
        } finally {
            aborted.set(true)
            // The published target is never deleted on error, only our own
            // incomplete staging file. No recursive cleanup.
            partial?.let { if (it.exists()) it.delete() }
            try { engine?.shutdown() } catch (_: Exception) {}
            executor.shutdownNow()
            busy.set(false)
        }
    }
}
