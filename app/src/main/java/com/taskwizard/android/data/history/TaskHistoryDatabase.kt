package com.taskwizard.android.data.history

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * Room database for task history
 *
 * Configuration:
 * - Version 2 (added apiContextMessagesJson for conversation continuation)
 * - Single table: task_history
 * - Destructive migration for development
 * - Callback for data seeding in future
 */
@Database(
    entities = [TaskHistoryEntity::class],
    version = 2,
    exportSchema = false
)
abstract class TaskHistoryDatabase : RoomDatabase() {
    /**
     * Get the DAO for task history operations
     */
    abstract fun historyDao(): TaskHistoryDao

    companion object {
        private const val DATABASE_NAME = "task_history.db"

        @Volatile
        private var INSTANCE: TaskHistoryDatabase? = null

        /**
         * Get the singleton database instance
         * Uses double-checked locking for thread safety
         *
         * @param context Application context
         * @return Database instance
         */
        fun getDatabase(context: Context): TaskHistoryDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    TaskHistoryDatabase::class.java,
                    DATABASE_NAME
                )
                    // Fallback to destructive migration for development
                    // TODO: Implement proper migrations for production
                    .fallbackToDestructiveMigration()
                    // Add callback for database initialization
                    .addCallback(DatabaseCallback())
                    .build()
                INSTANCE = instance
                instance
            }
        }

        /**
         * Close the database instance
         * Useful for testing
         */
        fun closeDatabase() {
            INSTANCE?.close()
            INSTANCE = null
        }

        /**
         * Database callback for initialization
         * Can be used for data seeding or logging
         */
        private class DatabaseCallback : RoomDatabase.Callback() {
            override fun onCreate(db: SupportSQLiteDatabase) {
                super.onCreate(db)
                // Database created - can be used for initial data seeding
            }

            override fun onOpen(db: SupportSQLiteDatabase) {
                super.onOpen(db)
                // Database opened - can be used for integrity checks
            }
        }
    }
}
