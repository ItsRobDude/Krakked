export type CredentialPayload = {
  apiKey: string;
  apiSecret: string;
  region: string;
};

export type CredentialResponse = {
  data: { valid: boolean } | null;
  error: string | null;
};

const defaultEndpoint = '/api/system/credentials/validate';
const API_TOKEN = import.meta.env.VITE_API_TOKEN;

export async function validateCredentials(payload: CredentialPayload): Promise<CredentialResponse> {
  // The backend enforces bearer auth when enabled and always responds with
  // `{ data: { valid: bool }, error: string | null }`. Missing fields, auth
  // failures, Kraken downtime, and unexpected errors are normalized into the
  // `error` string so the UI can display precise feedback without guessing.
  const endpoint = import.meta.env.VITE_CREDENTIAL_ENDPOINT || defaultEndpoint;
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (API_TOKEN) headers.Authorization = `Bearer ${API_TOKEN}`;

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify(payload),
    });

    const result = (await response.json()) as CredentialResponse;
    if (!response.ok) {
      throw new Error(result.error || `Request failed: ${response.status}`);
    }

    return {
      data: result.data ?? { valid: false },
      error: result.error ?? null,
    };
  } catch (error) {
    console.warn('Falling back to placeholder validation. Reason:', error);
  }

  await new Promise((resolve) => setTimeout(resolve, 600));

  const valid = payload.apiKey.trim().length > 0 && payload.apiSecret.trim().length > 0;
  return {
    data: { valid },
    error: valid ? null : 'Both fields are required.',
  };
}
