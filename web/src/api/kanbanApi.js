import axios from 'axios';

import API_BASE from '../config';

export async function postTaskComment(taskId, payload) {
  const response = await axios.post(
    `${API_BASE}/multi-agent/tasks/${encodeURIComponent(taskId)}/comments`,
    payload,
  );
  return response.data;
}
