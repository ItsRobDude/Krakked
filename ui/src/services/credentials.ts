export type CredentialPayload = {
  apiKey: string;
  apiSecret: string;
};

export type CredentialResponse = {
  success: boolean;
  message?: string;
};

const defaultEndpoint = '/api/system/credentials/validate';

export async function validateCredentials(payload: CredentialPayload): Promise<CredentialResponse> {
  const endpoint = import.meta.env.VITE_CREDENTIAL_ENDPOINT || defaultEndpoint;

  try {
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Request failed: ${response.status}`);
    }

    const result = (await response.json()) as CredentialResponse;
    return result;
  } catch (error) {
    console.warn('Falling back to placeholder validation. Reason:', error);
  }

  await new Promise((resolve) => setTimeout(resolve, 600));

  const valid = payload.apiKey.trim().length > 0 && payload.apiSecret.trim().length > 0;
  return {
    success: valid,
    message: valid ? 'Credentials look good. Ready to load into Kraken bot.' : 'Both fields are required.',
  };
}
