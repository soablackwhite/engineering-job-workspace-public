CREATE TABLE `tracking` (
	`id` text PRIMARY KEY NOT NULL,
	`status` text DEFAULT 'To review' NOT NULL,
	`notes` text DEFAULT '' NOT NULL,
	`follow_up` text DEFAULT '' NOT NULL,
	`updated_at` text NOT NULL
);
