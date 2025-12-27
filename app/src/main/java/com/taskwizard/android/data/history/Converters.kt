package com.taskwizard.android.data.history

import androidx.room.TypeConverter
import com.google.gson.Gson
import com.google.gson.reflect.TypeToken
import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem

/**
 * Type converters for Room database
 *
 * Handles serialization/deserialization of complex types:
 * - MessageItem (sealed class) -> JSON
 * - Action (data class) -> JSON
 * - String lists -> JSON
 */

/**
 * Converter for MessageItem list
 * Handles sealed class serialization with proper type discriminator
 */
class MessageListConverter {
    private val gson = Gson()

    @TypeConverter
    fun fromMessageList(messages: List<MessageItem>): String {
        return gson.toJson(messages)
    }

    @TypeConverter
    fun toMessageList(json: String): List<MessageItem> {
        val listType = object : TypeToken<List<MessageItem>>() {}.type
        return gson.fromJson(json, listType)
    }
}

/**
 * Converter for Action list
 */
class ActionListConverter {
    private val gson = Gson()

    @TypeConverter
    fun fromActionList(actions: List<Action>): String {
        return gson.toJson(actions)
    }

    @TypeConverter
    fun toActionList(json: String): List<Action> {
        val listType = object : TypeToken<List<Action>>() {}.type
        return gson.fromJson(json, listType)
    }
}

/**
 * Converter for String list (error messages)
 */
class StringListConverter {
    private val gson = Gson()

    @TypeConverter
    fun fromStringList(list: List<String>): String {
        return gson.toJson(list)
    }

    @TypeConverter
    fun toStringList(json: String): List<String> {
        val listType = object : TypeToken<List<String>>() {}.type
        return gson.fromJson(json, listType)
    }
}

/**
 * Converter for TaskStatus enum
 */
class TaskStatusConverter {
    @TypeConverter
    fun fromTaskStatus(status: TaskStatus): String {
        return status.name
    }

    @TypeConverter
    fun toTaskStatus(value: String): TaskStatus {
        return try {
            TaskStatus.valueOf(value)
        } catch (e: IllegalArgumentException) {
            TaskStatus.PENDING
        }
    }
}
