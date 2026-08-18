import adapter from '@sveltejs/adapter-node';

/** @type {import('@sveltejs/kit').Config} */
const config = {
	kit: {
		adapter: adapter({
			out: 'build'
		}),
		csrf: {
			// Allow LAN access on classroom machines behind the Mac Studio's IP.
			trustedOrigins: ['*']
		}
	}
};

export default config;
