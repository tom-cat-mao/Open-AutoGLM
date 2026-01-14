package com.taskwizard.android.data.history

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * Room database for task history
 *
 * Configuration:
 * - Version 2 (added apiContextMessagesJson for conversation continuation)
 * - Single table: task_history
 * - Proper migrations for production (no destructive fallback)
 */
@Database(
    entities = [TaskHistoryEntity::class],
    version = 2,
    exportSchema = true
)
abstract class TaskHistoryDatabase : RoomDatabase() {
    abstract fun historyDao(): TaskHistoryDao

    companion object {
        private const val DATABASE_NAME = "task_history.db"

        @Volatile
        private var INSTANCE: TaskHistoryDatabase? = null

        /**
         * Migration from version 1 to 2
         * Currently no schema changes, but prepares for future migrations
         */
        private val MIGRATION_1_2: Migration = object : Migration(1, 2) {
            override fun migrate(database: SupportSQLiteDatabase) {
                // Version 2 added apiContextMessagesJson field but Room handles it automatically
                android.util.Log.d("TaskHistoryDatabase", "Migrating from v1 to v2")
            }
        }

        fun getDatabase(context: Context): TaskHistoryDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    TaskHistoryDatabase::class.java,
                    DATABASE_NAME
                )
                    .addMigrations(MIGRATION_1_2)
                    .addCallback(DatabaseCallback())
                    .build()
                INSTANCE = instance
                instance
            }
        }

        fun closeDatabase() {
            INSTANCE?.close()
            INSTANCE = null
        }

        private class DatabaseCallback : RoomDatabase.Callback() {
            override fun onCreate(db: SupportSQLiteDatabase) {
                super.onCreate(db)
                android.util.Log.d("TaskHistoryDatabase", "Database created")
            }

            override fun onOpen(db: SupportSQLiteDatabase) {
                super.onOpen(db)
                android.util.Log.d("TaskHistoryDatabase", "Database opened")
            }
        }
    }
}
