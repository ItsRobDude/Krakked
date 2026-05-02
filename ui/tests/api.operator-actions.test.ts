import { afterEach, describe, expect, test, vi } from 'vitest';
import { flattenAllPositions } from '../src/services/api';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('operator action API requests', () => {
  test('sends the flatten confirmation token expected by the backend', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          data: {
            success: true,
            errors: [],
            warnings: [],
            orders: [],
          },
          error: null,
        }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    vi.stubGlobal('fetch', fetchMock);

    await flattenAllPositions();

    expect(fetchMock).toHaveBeenCalledWith(
      '/api/execution/flatten_all',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ confirmation: 'FLATTEN ALL' }),
      }),
    );
  });
});
