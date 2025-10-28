import 'dotenv/config';
import express from 'express';
import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { SSEServerTransport } from '@modelcontextprotocol/sdk/server/sse.js';
import { z } from 'zod';

const app = express();
const PORT = process.env.PORT || 3000;

const mcp = new McpServer({ name: 'hello-mcp', version: '1.0.0' });

// 最小ツール：say_hello
mcp.registerTool(
  'say_hello',
  {
    title: 'Say Hello',
    description: 'Return greeting text',
    inputSchema: { name: z.string() },
    outputSchema: { message: z.string() }, // 任意だが付けておくと型が安定
  },
  async ({ name }) => {
    const message = `Hello, ${name}! 👋`;
    return {
      content: [{ type: 'text', text: message }],
      structuredContent: { message },
    };
  }
);

// ---- SSEエンドポイント ----
let transport = null;
app.get('/sse', (req, res) => {
  // SSEのヘッダは明示
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('Content-Type', 'text/event-stream');

  transport = new SSEServerTransport('/messages', res);

  // 切断時クリーンアップ
  req.on('close', () => { transport = null; });

  mcp.connect(transport);
});

// /messages は body parser を噛ませない
app.post('/messages', (req, res) => {
  if (!transport) return res.status(500).json({ error: 'SSE not initialized' });
  try { transport.handlePostMessage(req, res); }
  catch (e) { console.error(e); res.status(500).json({ error: String(e) }); }
});

app.get('/health', (_req, res) => res.json({ ok: true }));
app.listen(PORT, () => console.log(`✅ MCP Server running on http://localhost:${PORT}`));