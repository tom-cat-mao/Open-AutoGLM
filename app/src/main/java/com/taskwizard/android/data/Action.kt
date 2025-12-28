package com.taskwizard.android.data

import androidx.compose.runtime.Stable
import kotlinx.collections.immutable.ImmutableList

@Stable
data class Action(
    val action: String?,
    val location: ImmutableList<Int>? = null,
    val content: String? = null,
    val message: String? = null,      // 用于 Take_over, finish 等的消息
    val duration: Int? = null,        // 用于 Long Press 的持续时间（毫秒）
    val instruction: String? = null   // 用于 Call_API 的指令
)
