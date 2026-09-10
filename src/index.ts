import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';
import { callBridge, type BridgeReply } from './bridge.js';

const VERSION = '0.1.6';

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
      description: 'Compact state. status is a queue-bypassing health probe. Scene intelligence: tree, find:TERM, oN:spatial. Also summary|objects|selection|materials|refs|ref:rN|quality|quality:oN|oN[:mesh|uv|mat|rig|bounds|rings]|collections|collection:NAME|api:bpy.ops.*.',
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
      description: "Short guarded bpy batch; split large work into 1-3 logical objects. O('oN') resolves objects, REF('rN') references, and M has reusable material/mesh/tube/clump/panel/ellipsoid helpers. checkpoint=true forces a recovery copy. Python batches have a cooperative deadline; never replay a timeout before inspect.",
      inputSchema: z.object({
        code: z.string().min(1).max(24_576),
        checkpoint: z.boolean().optional(),
      }),
      annotations: { destructiveHint: true, idempotentHint: false },
    },
    async ({ code, checkpoint }) => {
      try {
        return textResult(await callBridge<BridgeReply>('/apply', { code, checkpoint }, 35_000));
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    'render',
    {
      description: 'Validation images. fast/lookdev/wire are controlled scene views; viewport captures the current visible 3D editor (GUI only). Reference ref=rN or refs=[rN..] returns approved source images directly.',
      inputSchema: z.object({
        views: z.array(z.enum(['front', 'side', 'back', 'three_quarter'])).max(4).optional(),
        ids: z.array(z.string().regex(/^o\d+$/)).max(128).optional(),
        size: z.number().int().min(128).max(1024).optional(),
        mode: z.enum(['fast', 'lookdev', 'wire', 'viewport']).optional(),
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
