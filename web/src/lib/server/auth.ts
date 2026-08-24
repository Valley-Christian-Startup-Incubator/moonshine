import { createHash, timingSafeEqual } from 'node:crypto';
import { WEB_PASSWORD } from './env';

export const WEB_SESSION_COOKIE = 'distill_session';

function digest(value: string): Buffer {
	return createHash('sha256').update(value).digest();
}

export function isWebAuthEnabled(): boolean {
	return WEB_PASSWORD.length > 0;
}

export function isWebPasswordValid(candidate: string): boolean {
	return timingSafeEqual(digest(candidate), digest(WEB_PASSWORD));
}

export function webSessionToken(): string {
	return digest(WEB_PASSWORD).toString('hex');
}

export function isWebSessionValid(value: string | undefined): boolean {
	if (!value || !isWebAuthEnabled()) return false;
	return timingSafeEqual(digest(value), digest(webSessionToken()));
}
