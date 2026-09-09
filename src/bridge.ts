import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const STATE_FILE = process.env.P178292_STATE_FILE ?? join(tmpdir(), '1782-92-bridge.json');

interface BridgeState {
  host: '127.0.0.1';
  port: number;
  token: string;
  pid: number;
  version: string;
}

export interface BridgeImage {
  view: string;
  mime: 'image/png';
  data: string;
}

export interface BridgeReply {
  ok: boolean;
  rev?: number;
  error?: string;
  message?: string;
  images?: BridgeImage[];
  [key: string]: unknown;
}

async function readState(): Promise<BridgeState> {
  let raw: string;
  try {
    raw = await readFile(STATE_FILE, 'utf8');
  } catch {
    throw new Error('blender_offline');
  }

  const state = JSON.parse(raw) as Partial<BridgeState>;
  if (
    state.host !== '127.0.0.1' ||
    !Number.isInteger(state.port) ||
    typeof state.token !== 'string' ||
    state.token.length < 16
  ) {
    throw new Error('bridge_state_invalid');
  }
  return state as BridgeState;
}

export async function callBridge<T extends BridgeReply>(
  path: '/inspect' | '/apply' | '/render',
  body: Record<string, unknown>,
  timeoutMs = 180_000,
): Promise<T> {
  const state = await readState();
  const res = await fetch(`http://${state.host}:${state.port}${path}`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-1782-token': state.token,
    },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(timeoutMs),
  });

  let payload: BridgeReply;
  try {
    payload = (await res.json()) as BridgeReply;
  } catch {
    throw new Error(`bridge_http_${res.status}`);
  }

  if (!res.ok || payload.ok === false) {
    throw new Error(payload.error ?? `bridge_http_${res.status}`);
  }
  return payload as T;
}
