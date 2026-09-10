import { McpServer } from '@modelcontextprotocol/server';
import { serveStdio } from '@modelcontextprotocol/server/stdio';
import * as z from 'zod/v4';
import { callBridge, type BridgeReply } from './bridge.js';

const VERSION = '0.1.2';

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
      description: 'Compact state. q=summary|objects|selection|materials|refs|quality|quality:oN|oN[:mesh|uv|mat|rig]|api:bpy.ops.*.',
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
      description: "Batch guarded bpy. O('oN') resolves objects; REF('rN') loads only user-approved references.",
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
      description: 'Validation PNGs. fast=shape, lookdev=materials, wire=topology. Default front + 3/4.',
      inputSchema: z.object({
        views: z.array(z.enum(['front', 'side', 'back', 'three_quarter'])).max(4).optional(),
        ids: z.array(z.string().regex(/^o\d+$/)).max(128).optional(),
        size: z.number().int().min(128).max(1024).optional(),
        mode: z.enum(['fast', 'lookdev', 'wire']).optional(),
      }),
      annotations: { readOnlyHint: true, idempotentHint: true },
    },
    async ({ views, ids, size, mode }) => {
      try {
        const reply = await callBridge<BridgeReply>(
          '/render',
          { views: views ?? ['front', 'three_quarter'], ids, size: size ?? 512, mode: mode ?? 'fast' },
          240_000,
        );
        const images = reply.images ?? [];
        return {
          content: [
            {
              type: 'text' as const,
              text: JSON.stringify({ rev: reply.rev, mode: mode ?? 'fast', views: images.map((image) => image.view) }),
            },
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
