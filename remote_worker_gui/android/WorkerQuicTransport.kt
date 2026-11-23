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
 * One transfer, no persistent HTTP body cache, exactly one app-private GGUF.
 */
object WorkerQuicTransport {
    private const val MAX_BYTES = 16L * 1024 * 1024 * 1024
    private val busy = AtomicBoolean(false)
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
        check(busy.compareAndSet(false, true)) { "GGUF transfer already active" }
        var partial: File? = null
        var request: UrlRequest? = null
        var engine: HttpEngine? = null
        val executor = Executors.newSingleThreadExecutor()
        try {
            val root = File(context.filesDir, "worker-gguf-models")
            check((root.isDirectory || root.mkdir()) && !java.nio.file.Files.isSymbolicLink(root.toPath()))
            val complete = File(root, "$sha256.gguf")
            check(!complete.exists()) { "GGUF target exists; validate/reconcile through Rust" }
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
            var redirects = 0
            val sha = MessageDigest.getInstance("SHA-256")
            val stream = FileOutputStream(pending, false)
            val buffer = ByteBuffer.allocateDirect(64 * 1024)
            val callback = object : UrlRequest.Callback {
                override fun onRedirectReceived(
                    request: UrlRequest, info: UrlResponseInfo, newLocationUrl: String
                ) {
                    try {
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
                        check(info.httpStatusCode == 200) { "Unexpected GGUF status" }
                        val reportedLength = info.headers.asMap.entries
                            .firstOrNull { it.key.equals("content-length", ignoreCase = true) }
                            ?.value?.firstOrNull()?.toLongOrNull()
                        if (reportedLength != null) {
                            check(reportedLength == expectedBytes) { "GGUF length mismatch" }
                        }
                        canonicalUri(info.url)
                        protocol = info.negotiatedProtocol ?: ""
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
                        byteBuffer.flip()
                        val count = byteBuffer.remaining()
                        received = Math.addExact(received, count.toLong())
                        check(received <= expectedBytes) { "GGUF exceeds approved size" }
                        check(root.usableSpace >=
                            Math.addExact(expectedBytes - received, reserveBytes)) {
                            "insufficient_storage"
                        }
                        val bytes = ByteArray(count)
                        byteBuffer.get(bytes)
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
                        stream.fd.sync()
                        check(received == expectedBytes) { "Truncated GGUF" }
                        val actual = sha.digest().joinToString("") { "%02x".format(it) }
                        check(actual == sha256) { "GGUF SHA-256 mismatch" }
                        check(root.usableSpace >= reserveBytes) { "insufficient_storage" }
                        check(!complete.exists()) { "GGUF target already exists" }
                        check(pending.renameTo(complete)) { "Could not publish verified GGUF" }
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
                check(done.await(2, TimeUnit.HOURS)) { "GGUF transfer timed out" }
            } finally {
                request?.cancel()
                stream.close()
            }
            check(error == null) { error ?: "GGUF transfer failed" }
            return protocol
        } finally {
            // The published target is never deleted on error, only our own
            // incomplete staging file. No recursive cleanup.
            partial?.let { if (it.exists()) it.delete() }
            try { engine?.shutdown() } catch (_: Exception) {}
            executor.shutdownNow()
            busy.set(false)
        }
    }
}
