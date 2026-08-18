import type { Config } from 'tailwindcss';

export default {
	darkMode: 'class',
	content: ['./src/**/*.{html,js,svelte,ts}'],
	theme: {
		extend: {
			colors: {
				bg: {
					DEFAULT: '#0a0a0b',
					subtle: '#111113',
					elevated: '#18181b'
				},
				border: {
					DEFAULT: '#27272a',
					subtle: '#1f1f22'
				}
			},
			fontFamily: {
				mono: [
					'"JetBrains Mono"',
					'ui-monospace',
					'SFMono-Regular',
					'Menlo',
					'monospace'
				],
				sans: [
					'-apple-system',
					'BlinkMacSystemFont',
					'"Inter"',
					'"Segoe UI"',
					'sans-serif'
				]
			}
		}
	},
	plugins: []
} satisfies Config;
