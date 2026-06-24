/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_LLM_GATEWAY_API_KEY?: string;
  readonly VITE_LLM_GATEWAY_BACKEND_URL?: string;
  readonly VITE_LLM_GATEWAY_MODEL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
