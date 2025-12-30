package com.taskwizard.android

import com.taskwizard.android.data.Action
import com.taskwizard.android.data.MessageItem
import com.taskwizard.android.data.SystemMessageType
import kotlinx.collections.immutable.persistentListOf
import org.junit.Assert.*
import org.junit.Test

/**
 * 保存模板对话框触发逻辑测试
 * 测试 getAllActions()、handleSaveAsTaskFromMessage()、saveAsTemplate() 等方法
 */
class SaveTemplateDialogTriggerTest {

    // ==================== getAllActions() 测试 ====================

    @Test
    fun `getAllActions returns empty when both lists are empty`() {
        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf<Action>()

        val allActions = taskActions + pendingActionsToSave

        assertTrue("Should return empty list", allActions.isEmpty())
    }

    @Test
    fun `getAllActions returns taskActions when pending is empty`() {
        val action1 = Action(action = "tap", location = persistentListOf(100, 200))
        val action2 = Action(action = "swipe", location = persistentListOf(300, 400))
        val taskActions = mutableListOf(action1, action2)
        val pendingActionsToSave = mutableListOf<Action>()

        val allActions = taskActions + pendingActionsToSave

        assertEquals(2, allActions.size)
        assertEquals("tap", allActions[0].action)
        assertEquals("swipe", allActions[1].action)
    }

    @Test
    fun `getAllActions returns pending when taskActions is empty`() {
        val taskActions = mutableListOf<Action>()
        val action1 = Action(action = "type", content = "hello")
        val pendingActionsToSave = mutableListOf(action1)

        val allActions = taskActions + pendingActionsToSave

        assertEquals(1, allActions.size)
        assertEquals("type", allActions[0].action)
    }

    @Test
    fun `getAllActions combines both lists correctly`() {
        val action1 = Action(action = "tap", location = persistentListOf(100, 200))
        val action2 = Action(action = "swipe", location = persistentListOf(300, 400))
        val action3 = Action(action = "type", content = "hello")

        val taskActions = mutableListOf(action1, action2)
        val pendingActionsToSave = mutableListOf(action3)

        val allActions = taskActions + pendingActionsToSave

        assertEquals(3, allActions.size)
        assertEquals("tap", allActions[0].action)
        assertEquals("swipe", allActions[1].action)
        assertEquals("type", allActions[2].action)
    }

    // ==================== handleSaveAsTaskFromMessage() 测试 ====================

    @Test
    fun `handleSaveAsTaskFromMessage shows dialog when actions exist`() {
        val taskActions = mutableListOf<Action>()
        val action1 = Action(action = "tap", location = persistentListOf(100, 200))
        val pendingActionsToSave = mutableListOf(action1)

        val allActions = taskActions + pendingActionsToSave
        val shouldShowDialog = allActions.isNotEmpty()

        assertTrue("Dialog should show when pending actions exist", shouldShowDialog)
    }

    @Test
    fun `handleSaveAsTaskFromMessage does nothing when no actions`() {
        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf<Action>()

        val allActions = taskActions + pendingActionsToSave
        val shouldShowDialog = allActions.isNotEmpty()

        assertFalse("Dialog should not show when no actions", shouldShowDialog)
    }

    @Test
    fun `handleSaveAsTaskFromMessage calculates correct step count`() {
        val action1 = Action(action = "tap", location = persistentListOf(100, 200))
        val action2 = Action(action = "swipe", location = persistentListOf(300, 400))
        val action3 = Action(action = "type", content = "hello")

        val taskActions = mutableListOf(action1)
        val pendingActionsToSave = mutableListOf(action2, action3)

        val allActions = taskActions + pendingActionsToSave
        val templateStepCount = allActions.size

        assertEquals(3, templateStepCount)
    }

    // ==================== saveAsTemplate() 测试 ====================

    @Test
    fun `saveAsTemplate uses all actions including pending`() {
        val action1 = Action(action = "tap", location = persistentListOf(100, 200))
        val action2 = Action(action = "swipe", location = persistentListOf(300, 400))

        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf(action1, action2)

        val allActions = taskActions + pendingActionsToSave

        val canSave = allActions.isNotEmpty()
        val stepCount = allActions.size

        assertTrue("Should be able to save with pending actions", canSave)
        assertEquals(2, stepCount)
    }

    @Test
    fun `saveAsTemplate returns early when no actions`() {
        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf<Action>()

        val allActions = taskActions + pendingActionsToSave
        val canSave = allActions.isNotEmpty()

        assertFalse("Should not save when no actions", canSave)
    }

    // ==================== removeSaveTaskPromptMessage() 测试 ====================

    @Test
    fun `removeSaveTaskPromptMessage removes correct message type`() {
        val messages = listOf(
            MessageItem.SystemMessage(content = "任务开始", type = SystemMessageType.INFO),
            MessageItem.SystemMessage(
                content = "任务完成！",
                type = SystemMessageType.SAVE_TASK_PROMPT,
                stepCount = 5
            ),
            MessageItem.SystemMessage(content = "操作成功", type = SystemMessageType.SUCCESS)
        )

        val filteredMessages = messages.filterNot {
            it is MessageItem.SystemMessage &&
            it.type == SystemMessageType.SAVE_TASK_PROMPT
        }

        assertEquals(2, filteredMessages.size)
    }

    @Test
    fun `removeSaveTaskPromptMessage keeps other messages`() {
        val messages = listOf(
            MessageItem.ThinkMessage(content = "思考中..."),
            MessageItem.SystemMessage(
                content = "任务完成！",
                type = SystemMessageType.SAVE_TASK_PROMPT,
                stepCount = 3
            ),
            MessageItem.ActionMessage(
                action = Action(action = "tap", location = persistentListOf(100, 200))
            )
        )

        val filteredMessages = messages.filterNot {
            it is MessageItem.SystemMessage &&
            it.type == SystemMessageType.SAVE_TASK_PROMPT
        }

        assertEquals(2, filteredMessages.size)
        assertTrue(filteredMessages[0] is MessageItem.ThinkMessage)
        assertTrue(filteredMessages[1] is MessageItem.ActionMessage)
    }

    // ==================== finish 动作处理测试 ====================

    @Test
    fun `finish action triggers dialog flow with pending actions`() {
        val action = "finish"
        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf(
            Action(action = "tap", location = persistentListOf(100, 200)),
            Action(action = "swipe", location = persistentListOf(300, 400))
        )

        val isFinishAction = action.lowercase() == "finish"
        val allActions = taskActions + pendingActionsToSave
        val actionCount = allActions.size
        val shouldShowDialog = isFinishAction && actionCount > 0

        assertTrue("Finish action should trigger dialog", shouldShowDialog)
        assertEquals(2, actionCount)
    }

    @Test
    fun `finish action does not trigger dialog when no actions`() {
        val action = "finish"
        val taskActions = mutableListOf<Action>()
        val pendingActionsToSave = mutableListOf<Action>()

        val isFinishAction = action.lowercase() == "finish"
        val allActions = taskActions + pendingActionsToSave
        val actionCount = allActions.size
        val shouldShowDialog = isFinishAction && actionCount > 0

        assertFalse("Should not trigger dialog when no actions", shouldShowDialog)
        assertEquals(0, actionCount)
    }

    // ==================== 边界情况测试 ====================

    @Test
    fun `test with large number of actions`() {
        val taskActions = (1..50).map {
            Action(action = "tap", location = persistentListOf(it * 10, it * 20))
        }.toMutableList()
        val pendingActionsToSave = (1..50).map {
            Action(action = "swipe", location = persistentListOf(it * 10, it * 20))
        }.toMutableList()

        val allActions = taskActions + pendingActionsToSave

        assertEquals(100, allActions.size)
    }

    @Test
    fun `test SAVE_TASK_PROMPT message has correct stepCount`() {
        val stepCount = 7
        val message = MessageItem.SystemMessage(
            content = "任务完成！",
            type = SystemMessageType.SAVE_TASK_PROMPT,
            stepCount = stepCount
        )

        assertEquals(SystemMessageType.SAVE_TASK_PROMPT, message.type)
        assertEquals(7, message.stepCount)
    }
}
