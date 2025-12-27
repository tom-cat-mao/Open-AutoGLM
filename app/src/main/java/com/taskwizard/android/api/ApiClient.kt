package com.taskwizard.android.api

import com.taskwizard.android.BuildConfig
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.logging.HttpLoggingInterceptor
import retrofit2.Retrofit
import retrofit2.converter.gson.GsonConverterFactory
import java.util.concurrent.TimeUnit

object ApiClient {
    private var retrofit: Retrofit? = null
    private var currentBaseUrl: String = "https://api.openai.com/v1/"
    private var currentApiKey: String = ""

    fun init(baseUrl: String, apiKey: String) {
        currentBaseUrl = if (baseUrl.endsWith("/")) baseUrl else "$baseUrl/"
        currentApiKey = apiKey
        retrofit = null // Reset to force rebuild
    }

    fun getService(): LLMService {
        if (retrofit == null) {
            // Security: Only log headers in debug mode, not full body (which contains API keys)
            // In production, use BASIC level to minimize sensitive data exposure
            val logging = HttpLoggingInterceptor().apply {
                level = if (BuildConfig.DEBUG) {
                    HttpLoggingInterceptor.Level.HEADERS  // Only headers in debug
                } else {
                    HttpLoggingInterceptor.Level.BASIC    // Minimal in production
                }
            }

            val authInterceptor = Interceptor { chain ->
                val request = chain.request().newBuilder()
                    .addHeader("Authorization", "Bearer $currentApiKey")
                    .addHeader("Content-Type", "application/json")
                    .build()
                chain.proceed(request)
            }

            val client = OkHttpClient.Builder()
                .addInterceptor(authInterceptor)
                .addInterceptor(logging)
                .connectTimeout(60, TimeUnit.SECONDS)
                .readTimeout(60, TimeUnit.SECONDS)
                .writeTimeout(60, TimeUnit.SECONDS)
                .build()

            retrofit = Retrofit.Builder()
                .baseUrl(currentBaseUrl)
                .client(client)
                .addConverterFactory(GsonConverterFactory.create())
                .build()
        }
        return retrofit?.create(LLMService::class.java)
            ?: throw IllegalStateException("ApiClient not initialized")
    }
}
