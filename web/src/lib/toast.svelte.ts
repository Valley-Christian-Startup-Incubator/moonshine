interface Toast {
	id: number;
	message: string;
	kind: 'success' | 'error';
}

let nextId = 1;
const toasts = $state<Toast[]>([]);

export function toastState() {
	return toasts;
}

export function pushToast(message: string, kind: Toast['kind'] = 'success') {
	const id = nextId++;
	toasts.push({ id, message, kind });
	setTimeout(() => {
		const idx = toasts.findIndex((t) => t.id === id);
		if (idx !== -1) toasts.splice(idx, 1);
	}, 5000);
}
