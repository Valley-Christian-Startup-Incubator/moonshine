import type { AuthenticatedUser } from '$lib/server/auth';

declare global {
	namespace App {
		interface Locals {
			user: AuthenticatedUser | null;
		}
	}
}

export {};
