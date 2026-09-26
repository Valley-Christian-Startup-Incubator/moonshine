import { createHash, timingSafeEqual } from 'node:crypto';
import { ADMIN_PASSWORD, ADMIN_SESSION_COOKIE } from './env';

type Cookies = {
	get(name: string): string | undefined;
	set(name: string, value: string, opts: { path: string; httpOnly: boolean; sameSite: 'strict'; secure: boolean; maxAge: number }): void;
	delete(name: string, opts: { path: string }): void;
};

const token = () => createHash('sha256').update(`moonshine-admin:${ADMIN_PASSWORD}`).digest('hex');

function same(a: string, b: string): boolean {
	const x = Buffer.from(a);
	const y = Buffer.from(b);
	return x.length === y.length && timingSafeEqual(x, y);
}

export function isAdmin(cookies: Pick<Cookies, 'get'>): boolean {
	const v = cookies.get(ADMIN_SESSION_COOKIE);
	return Boolean(ADMIN_PASSWORD) && typeof v === 'string' && same(v, token());
}

export function checkAdminPassword(password: unknown): boolean {
	if (!ADMIN_PASSWORD || typeof password !== 'string') return false;
	const a = createHash('sha256').update(password).digest();
	const b = createHash('sha256').update(ADMIN_PASSWORD).digest();
	return timingSafeEqual(a, b);
}

export function setAdminSession(cookies: Cookies, secure: boolean): void {
	cookies.set(ADMIN_SESSION_COOKIE, token(), { path: '/', httpOnly: true, sameSite: 'strict', secure, maxAge: 60 * 60 * 4 });
}

export function clearAdminSession(cookies: Pick<Cookies, 'delete'>): void {
	cookies.delete(ADMIN_SESSION_COOKIE, { path: '/' });
}
