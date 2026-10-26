import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';
export const tracking = sqliteTable('tracking', {
 id: text('id').primaryKey(), status: text('status').notNull().default('To review'),
 notes: text('notes').notNull().default(''), followUp: text('follow_up').notNull().default(''), updatedAt: text('updated_at').notNull(),
});

export const workflowState = sqliteTable("workflow_state", {
 id: integer("id").primaryKey(), revision: integer("revision").notNull(), payload: text("payload").notNull(),
});
