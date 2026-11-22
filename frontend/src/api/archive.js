import {send} from './client.js';
export const api = {
  login: data => send('/auth/login', data),
  sleeves: input => send('/sleeves', input),
  usage: code => send('/usage' + (code ? `?sleeve_code=${encodeURIComponent(code)}` : '')),
  recordUsage: (input, id) => send(id ? `/usage/${id}/amend` : '/usage', input),
  recommendations: input => send('/recommendations', input),
  recommendation: id => send(`/recommendations/${id}`),
  retry: id => send(`/recommendations/${id}/retry`, {}),
  publish: (id, note) => send(`/recommendations/${id}/publish`, {note}),
  export: id => send(`/recommendations/${id}/export`, undefined, true),
};
