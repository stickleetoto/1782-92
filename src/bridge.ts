import { readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const STATE_FILE = process.env.P178292_STATE_FILE ?? join(tmpdir(), '1782-92-bridge.json');
const STATE_TTL_MS = 1000;
const EXPECTED_BRIDGE_VERSION = '0.1.5';

interface BridgeState {
  host: '127.0.0.1';
  port: number;
  token: string;
  pid: number;
  version: string;
}

export interface BridgeImage {
  view: string;
  mime: string;
  data: string;
}

export interface BridgeReply {
  ok: boolean;
  rev?: number;
  error?: string;
  images?: BridgeImage[];
  [key: string]: unknown;
}

let cachedState: { state: BridgeState; until: number } | null = null;

function transportMessage(error: unknown): string {
  if (error instanceof Error) {
    if (error.name === 'TimeoutError' || error.name === 'AbortError') return 'bridge_timeout';
    if (error.message === 'fetch failed') return 'blender_unreachable';
    return error.message;
  }
  return String(error);
}

async function readState(force = false): Promise<BridgeState> {
  const now = Date.now();
  if (!force && cachedState && cachedState.until > now) {
    return cachedState.state;
  }

  let raw: string;
  try {
    raw = await readFile(STATE_FILE, 'utf8');
  } catch {
    cachedState = null;
    throw new Error('blender_offline');
  }

  const state = JSON.parse(raw) as Partial<BridgeState>;
  if (
    state.host !== '127.0.0.1' ||
    !Number.isInteger(state.port) ||
    typeof state.token !== 'string' ||
    state.token.length < 16 ||
    typeof state.version !== 'string'
  ) {
    cachedState = null;
    throw new Error('bridge_state_invalid');
  }
  if (state.version !== EXPECTED_BRIDGE_VERSION) {
    cachedState = null;
    throw new Error(`bridge_version_mismatch:${state.version}->${EXPECTED_BRIDGE_VERSION}:restart_blender_and_mcp`);
  }

  const valid = state as BridgeState;
  cachedState = { state: valid, until: now + STATE_TTL_MS };
  return valid;
}

async function postBridge<T extends BridgeReply>(
  state: BridgeState,
  path: '/inspect' | '/apply' | '/render',
  body: Record<string, unknown>,
  timeoutMs: number,
): Promise<{ response: Response; payload: T }> {
  let response: Response;
  try {
    response = await fetch(`http://${state.host}:${state.port}${path}`, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-1782-token': state.token,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    cachedState = null;
    throw new Error(transportMessage(error));
  }

  let payload: T;
  try {
    payload = (await response.json()) as T;
  } catch {
    throw new Error(`bridge_http_${response.status}`);
  }
  return { response, payload };
}

export async function callBridge<T extends BridgeReply>(
  path: '/inspect' | '/apply' | '/render',
  body: Record<string, unknown>,
  timeoutMs = 60_000,
): Promise<T> {
  let state = await readState();
  let result: { response: Response; payload: T };

  try {
    result = await postBridge<T>(state, path, body, timeoutMs);
  } catch (error) {
    // Never retry a mutating apply after an ambiguous transport failure.
    if (path === '/apply') throw error;
    state = await readState(true);
    result = await postBridge<T>(state, path, body, timeoutMs);
  }

  if (result.response.status === 403) {
    // A stale token is safe to retry because Blender rejected before execution.
    state = await readState(true);
    result = await postBridge<T>(state, path, body, timeoutMs);
  }

  if (!result.response.ok || result.payload.ok === false) {
    throw new Error(result.payload.error ?? `bridge_http_${result.response.status}`);
  }
  return result.payload;
}
