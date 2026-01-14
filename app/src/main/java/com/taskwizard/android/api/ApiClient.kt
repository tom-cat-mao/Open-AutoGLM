package com.taskwizard.android.api

import com.taskwizard.android.BuildConfig
import okhttp3.ConnectionPool
import okhttp3.Dns
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.net.InetAddress
import java.util.concurrent.TimeUnit

/**
 * API Client with circuit breaker and retry mechanism
 *
 * Features:
 * - Circuit breaker pattern to prevent cascading failures
 * - Exponential backoff retry with configurable max retries
 * - Connection pooling for improved performance
 * - DNS caching for reduced latency
 */
object ApiClient {
    private const val TAG = "ApiClient"
    
    // Circuit breaker configuration
    private const val CIRCUIT_TIMEOUT_MS = 30000L  // 30 seconds before trying again
    private const val CIRCUIT_FAILURE_THRESHOLD = 5  // Open after 5 failures
    
    // Retry configuration
    private const val MAX_RETRIES = 3
    private const val INITIAL_DELAY_MS = 1000L
    private const val MAX_DELAY_MS = 10000L
    
    // Connection pool configuration
    private const val MAX_IDLE_CONNECTIONS = 5
    private const val KEEP_ALIVE_DURATION_MINUTES = 5L
    
    // Mutable state protected by volatile
    @Volatile private var retrofit: Retrofit? = null
    @Volatile private var okHttpClient: OkHttpClient? = null
    @Volatile private var currentBaseUrl: String = "https://api.openai.com/v1/"
    @Volatile private var currentApiKey: String = ""
    
    // Circuit breaker state
    @Volatile private var circuitState: CircuitState = CircuitState.CLOSED
    @Volatile private var lastFailureTime = 0L
    @Volatile private var failureCount = 0

    /**
     * Initialize the API client with new configuration
     */
    fun init(baseUrl: String, apiKey: String) {
        // 关闭旧的连接池，防止资源泄漏
        okHttpClient?.let { oldClient ->
            oldClient.dispatcher.executorService.shutdown()
            oldClient.connectionPool.evictAll()
        }

        currentBaseUrl = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        currentApiKey = apiKey
        retrofit = null
        okHttpClient = null
        // Reset circuit breaker on reinitialization
        resetCircuitBreaker()
    }

    /**
     * Get the LLM service for API calls
     * @throws CircuitBreakerOpenException if circuit breaker is open
     * @throws IllegalStateException if not initialized
     */
    @Throws(CircuitBreakerOpenException::class)
    fun getService(): LLMService {
        // Check circuit breaker state
        checkCircuitBreaker()
        
        if (retrofit == null) {
            retrofit = createRetrofit()
        }
        return retrofit?.create(LLMService::class.java)
            ?: throw IllegalStateException("ApiClient not initialized")
    }

    /**
     * Execute API call with automatic retry and circuit breaker
     *
     * @param T Response type
     * @param apiCall Suspend function that makes the API call
     * @return Result containing success or failure with exception
     */
    suspend fun <T> executeWithRetry(
        apiCall: suspend () -> retrofit2.Response<T>
    ): Result<T> {
        var lastException: Exception? = null
        
        repeat(MAX_RETRIES) { attempt ->
            try {
                // Check circuit breaker before each attempt
                checkCircuitBreaker()
                
                val response = apiCall()
                
                if (response.isSuccessful) {
                    // Success: close circuit breaker
                    onSuccess()
                    return Result.success(response.body()!!)
                } else {
                    // HTTP error: determine if should retry
                    val shouldRetry = when (response.code()) {
                        408, 429, 500, 502, 503, 504 -> true  // Retryable
                        401, 403 -> false  // Auth errors - no retry
                        else -> attempt < MAX_RETRIES - 1
                    }
                    
                    if (!shouldRetry) {
                        return Result.failure(ApiException(response.code(), response.message()))
                    }
                    lastException = ApiException(response.code(), response.message())
                }
            } catch (e: Exception) {
                lastException = e
            }
            
            // Exponential backoff
            if (attempt < MAX_RETRIES - 1) {
                val delay = minOf(
                    INITIAL_DELAY_MS * (1 shl attempt),  // Exponential: 1s, 2s, 4s
                    MAX_DELAY_MS
                )
                kotlinx.coroutines.delay(delay)
            }
        }
        
        // All retries exhausted: open circuit breaker
        onFailure()
        return Result.failure(lastException ?: Exception("Max retries exceeded"))
    }

    /**
     * Check and update circuit breaker state
     */
    @Throws(CircuitBreakerOpenException::class)
    private fun checkCircuitBreaker() {
        when (circuitState) {
            CircuitState.OPEN -> {
                if (System.currentTimeMillis() - lastFailureTime > CIRCUIT_TIMEOUT_MS) {
                    // Try to half-open
                    circuitState = CircuitState.HALF_OPEN
                } else {
                    throw CircuitBreakerOpenException("Circuit breaker is open. API temporarily unavailable.")
                }
            }
            CircuitState.HALF_OPEN -> {
                // Allow one request to test
            }
            CircuitState.CLOSED -> {
                // Normal operation
            }
        }
    }

    /**
     * Handle successful API call
     */
    private fun onSuccess() {
        circuitState = CircuitState.CLOSED
        failureCount = 0
    }

    /**
     * Handle failed API call
     */
    private fun onFailure() {
        failureCount++
        if (failureCount >= CIRCUIT_FAILURE_THRESHOLD) {
            circuitState = CircuitState.OPEN
            lastFailureTime = System.currentTimeMillis()
        }
    }

    /**
     * Reset circuit breaker state
     */
    private fun resetCircuitBreaker() {
        circuitState = CircuitState.CLOSED
        failureCount = 0
        lastFailureTime = 0L
    }

    /**
     * Create Retrofit instance with optimized configuration
     */
    private fun createRetrofit(): Retrofit {
        // Configure logging - minimal in production
        val logging = HttpLoggingInterceptor().apply {
            level = if (BuildConfig.DEBUG) {
                HttpLoggingInterceptor.Level.HEADERS
            } else {
                HttpLoggingInterceptor.Level.BASIC
            }
        }

        // Authentication interceptor
        val authInterceptor = Interceptor { chain ->
            val request = chain.request().newBuilder()
                .addHeader("Authorization", "Bearer $currentApiKey")
                .addHeader("Content-Type", "application/json")
                .build()
            chain.proceed(request)
        }

        // Configure OkHttp with connection pooling and DNS caching
        val client = OkHttpClient.Builder()
            .addInterceptor(authInterceptor)
            .addInterceptor(logging)
            .connectTimeout(60, TimeUnit.SECONDS)
            .readTimeout(60, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            // Connection pooling for performance
            .connectionPool(ConnectionPool(
                maxIdleConnections = MAX_IDLE_CONNECTIONS,
                keepAliveDuration = KEEP_ALIVE_DURATION_MINUTES,
                timeUnit = TimeUnit.MINUTES
            ))
            // DNS caching to reduce latency
            .dns(object : Dns {
                override fun lookup(hostname: String): List<InetAddress> {
                    return Dns.SYSTEM.lookup(hostname).take(2)
                }
            })
            .build()

        // 保存 OkHttpClient 引用，以便后续清理
        okHttpClient = client

        return Retrofit.Builder()
            .baseUrl(currentBaseUrl)
            .client(client)
            .addConverterFactory(GsonConverterFactory.create())
            .build()
    }

    /**
     * Shutdown API client and release resources
     * Call this in Application.onTerminate() or when app is closing
     */
    fun shutdown() {
        okHttpClient?.let { client ->
            client.dispatcher.executorService.shutdown()
            client.connectionPool.evictAll()
        }
        retrofit = null
        okHttpClient = null
    }

    /**
     * Get current circuit breaker status (for debugging)
     */
    fun getCircuitBreakerStatus(): String {
        return "CircuitBreaker(state=$circuitState, failures=$failureCount)"
    }
}

/**
 * Circuit breaker states
 */
private enum class CircuitState {
    CLOSED,    // Normal operation
    OPEN,      // Blocking requests
    HALF_OPEN  // Testing if service recovered
}

/**
 * Exception thrown when circuit breaker is open
 */
class CircuitBreakerOpenException(message: String) : Exception(message)

/**
 * Exception for API HTTP errors
 */
class ApiException(val code: Int, message: String) : Exception("API Error ($code): $message")
