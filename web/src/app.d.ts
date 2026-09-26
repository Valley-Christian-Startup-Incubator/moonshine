import type { AuthenticatedUser } from '$lib/server/auth';

declare global {
	namespace App {
		interface Locals {
			user: AuthenticatedUser | null;
			/** Selected person name (from the moonshine_user cookie), or null. */
			userName: string | null;
		}
	}
}

export {};
