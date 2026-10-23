export interface SemanticResult {
  id: string;
  score: number;
}

type WorkerMessage =
  | { type: 'ready'; requestId: number; result: SemanticResult[] }
  | { type: 'error'; requestId: number; error: string };

/** Lazy, offline semantic search backed by the same local ONNX model as the build. */
export class SemanticSearch {
  private worker?: Worker;
  private sequence = 0;
  private pending = new Map<number, { resolve: (value: SemanticResult[]) => void; reject: (reason: Error) => void }>();

  constructor(
    private readonly vectorIndexUrl = new URL(`${import.meta.env.BASE_URL}data/vectors.json`, window.location.href).href,
    private readonly topicsUrl = new URL(`${import.meta.env.BASE_URL}data/topics.json`, window.location.href).href,
  ) {}

  async search(query: string, topK = 20): Promise<SemanticResult[]> {
    const text = query.trim();
    if (!text) return [];
    const worker = this.getWorker();
    const requestId = ++this.sequence;
    return new Promise<SemanticResult[]>((resolve, reject) => {
      this.pending.set(requestId, { resolve, reject });
      worker.postMessage({ type: 'search', requestId, query: text, topK, indexUrl: this.vectorIndexUrl, topicsUrl: this.topicsUrl });
    });
  }

  dispose() {
    this.worker?.terminate();
    this.worker = undefined;
    for (const pending of this.pending.values()) pending.reject(new Error('Semantic search was disposed'));
    this.pending.clear();
  }

  private getWorker() {
    if (this.worker) return this.worker;
    const worker = new Worker(new URL('./semantic-worker.ts', import.meta.url), { type: 'module' });
    worker.onmessage = (event: MessageEvent<WorkerMessage>) => {
      const message = event.data;
      const pending = this.pending.get(message.requestId);
      if (!pending) return;
      this.pending.delete(message.requestId);
      if (message.type === 'ready') pending.resolve(message.result);
      else pending.reject(new Error(message.error));
    };
    worker.onerror = (event) => {
      // A crashed worker cannot process a later request. Only the currently
      // installed worker may reject pending work or clear the cache: a late
      // event from a terminated predecessor must not disrupt its replacement.
      if (this.worker !== worker) return;
      const error = new Error(event.message || 'Semantic search worker failed');
      worker.terminate();
      this.worker = undefined;
      for (const pending of this.pending.values()) pending.reject(error);
      this.pending.clear();
    };
    this.worker = worker;
    return worker;
  }
}

export const semanticSearch = new SemanticSearch();
