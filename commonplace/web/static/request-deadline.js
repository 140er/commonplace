// api includes response.json(): the deadline covers both transport and body decoding.
export async function boundedRequest(api, path, { timers = globalThis, signal, timeoutMs = 5000 } = {}) {
  const controller = new AbortController();
  let timer, abort;
  const deadline = new Promise((_, reject) => {
    abort = () => { controller.abort(); reject(Object.assign(new Error('Request cancelled'), { name: 'AbortError' })); };
    signal?.addEventListener('abort', abort, { once: true });
    if (signal?.aborted) abort();
    timer = timers.setTimeout(() => {
      controller.abort();
      reject(Object.assign(new Error('Request timed out'), { name: 'TimeoutError' }));
    }, timeoutMs);
  });
  try {
    if (controller.signal.aborted) return await deadline;
    return await Promise.race([deadline, api(path, { signal: controller.signal })]);
  } finally {
    timers.clearTimeout(timer);
    signal?.removeEventListener('abort', abort);
  }
}

