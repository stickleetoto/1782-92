import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';
import { callBridge, type BridgeReply } from './bridge.js';

const VERSION = '0.1.4';

function compact(value: BridgeReply): Record<string, unknown> {
  const { ok: _ok, ...rest } = value;
  return rest;
}

function textResult(value: BridgeReply) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(compact(value)) }],
  };
}

function errorResult(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return {
    content: [{ type: 'text' as const, text: JSON.stringify({ e: message }) }],
    isError: true,
  };
}

function createServer(): McpServer {
  const server = new McpServer({
    name: '1782-92',
    version: VERSION,
    description: 'Minimum tokens, maximum agency for 3D tools.',
  });

  server.registerTool(
    'inspect',
    {
      description: 'Compact state. q=summary|objects|selection|materials|refs|ref:rN|quality|oN[:mesh|uv|mat|rig|bounds|rings]|collections|collection:NAME|api:bpy.ops.*.',
      inputSchema: z.object({ q: z.string().max(128).optional() }),
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ q }) => {
      try {
        return textResult(await callBridge<BridgeReply>('/inspect', { q: q ?? 'summary' }, 15_000));
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    'apply',
    {
      description: "Batch guarded bpy. O('oN') resolves objects; REF('rN') loads only user-approved references. Read-only/no-op batches do not consume revisions.",
      inputSchema: z.object({ code: z.string().min(1).max(65_536) }),
      annotations: { destructiveHint: true, idempotentHint: false },
    },
    async ({ code }) => {
      try {
        return textResult(await callBridge<BridgeReply>('/apply', { code }, 180_000));
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    'render',
    {
      description: 'Validation images. Scene: fast/lookdev/wire. Reference: ref=rN or refs=[rN..] returns approved source images directly without scene edits.',
      inputSchema: z.object({
        views: z.array(z.enum(['front', 'side', 'back', 'three_quarter'])).max(4).optional(),
        ids: z.array(z.string().regex(/^o\d+$/)).max(128).optional(),
        size: z.number().int().min(128).max(1024).optional(),
        mode: z.enum(['fast', 'lookdev', 'wire']).optional(),
        ref: z.string().regex(/^r\d+$/).optional(),
        refs: z.array(z.string().regex(/^r\d+$/)).min(1).max(4).optional(),
      }),
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ views, ids, size, mode, ref, refs }) => {
      try {
        const referenceMode = ref !== undefined || refs !== undefined;
        const body = referenceMode
          ? { ref, refs }
          : { views: views ?? ['front', 'three_quarter'], ids, size: size ?? 512, mode: mode ?? 'fast' };
        const reply = await callBridge<BridgeReply>('/render', body, 240_000);
        const images = reply.images ?? [];
        const summary = referenceMode
          ? { rev: reply.rev, refs: images.map((image) => image.view) }
          : { rev: reply.rev, mode: mode ?? 'fast', views: images.map((image) => image.view) };
        return {
          content: [
            { type: 'text' as const, text: JSON.stringify(summary) },
            ...images.map((image) => ({
              type: 'image' as const,
              data: image.data,
              mimeType: image.mime,
            })),
          ],
        };
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  return server;
}

void serveStdio(createServer, {
  onerror(error) {
    process.stderr.write(`[1782-92] ${error.message}\n`);
  },
});
