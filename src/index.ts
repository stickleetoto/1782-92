import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';
import { callBridge, type BridgeReply } from './bridge.js';

const VERSION = '0.1.0';

function textResult(value: unknown) {
  return {
    content: [{ type: 'text' as const, text: JSON.stringify(value) }],
    structuredContent: value as Record<string, unknown>,
  };
}

function errorResult(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return {
    content: [{ type: 'text' as const, text: JSON.stringify({ ok: false, error: message }) }],
    isError: true,
  };
}

function createServer(): McpServer {
  const server = new McpServer({
    name: '1782-92',
    version: VERSION,
    description: 'Minimum tokens, maximum agency for Blender.',
  });

  server.registerTool(
    'inspect',
    {
      description: 'Compact Blender state. q: summary, objects, materials, selection, or object id.',
      inputSchema: z.object({ q: z.string().max(64).optional() }),
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ q }) => {
      try {
        const reply = await callBridge<BridgeReply>('/inspect', { q: q ?? 'summary' }, 15_000);
        return textResult(reply);
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    'apply',
    {
      description: 'Run guarded bpy code. Successful calls checkpoint automatically.',
      inputSchema: z.object({ code: z.string().min(1).max(65_536) }),
      annotations: { destructiveHint: true, idempotentHint: false },
    },
    async ({ code }) => {
      try {
        const reply = await callBridge<BridgeReply>('/apply', { code }, 180_000);
        return textResult(reply);
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  server.registerTool(
    'render',
    {
      description: 'Render compact orthographic validation views and return PNGs.',
      inputSchema: z.object({
        views: z.array(z.enum(['front', 'side', 'back', 'three_quarter'])).max(4).optional(),
        ids: z.array(z.string().regex(/^o\d+$/)).max(128).optional(),
        size: z.number().int().min(128).max(1024).optional(),
      }),
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ views, ids, size }) => {
      try {
        const reply = await callBridge<BridgeReply>(
          '/render',
          {
            views: views ?? ['front', 'side', 'back', 'three_quarter'],
            ids,
            size: size ?? 512,
          },
          240_000,
        );
        const images = reply.images ?? [];
        const meta = {
          ok: true,
          rev: reply.rev,
          views: images.map((image) => image.view),
        };
        return {
          content: [
            { type: 'text' as const, text: JSON.stringify(meta) },
            ...images.map((image) => ({
              type: 'image' as const,
              data: image.data,
              mimeType: image.mime,
            })),
          ],
          structuredContent: meta,
        };
      } catch (error) {
        return errorResult(error);
      }
    },
  );

  return server;
}

serveStdio(createServer).catch((error: unknown) => {
  process.stderr.write(`[1782-92] ${error instanceof Error ? error.message : String(error)}\n`);
  process.exitCode = 1;
});
