import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { Shield, UserCheck, UserX, RefreshCw, Heart, AlertTriangle, CheckCircle, XCircle, Info, Lock } from 'lucide-react';
import axios from 'axios';
import API_BASE from '../config';
import ConfirmModal from '../components/ConfirmModal';

/* ------------------------------------------------------------------ */
/*  Card                                                                */
/* ------------------------------------------------------------------ */
const Card = ({ children, style }) => (
  <div style={{
    background: 'rgba(255,255,255,0.03)',
    border: '1px solid rgba(255,255,255,0.08)',
    borderRadius: 16,
    padding: 24,
    ...style,
  }}>
    {children}
  </div>
);

/* ------------------------------------------------------------------ */
/*  Status icon for doctor checks                                      */
/* ------------------------------------------------------------------ */
const StatusIcon = ({ status }) => {
  const map = {
    ok:      <CheckCircle size={16} color="#4ade80" />,
    warning: <AlertTriangle size={16} color="#facc15" />,
    error:   <XCircle size={16} color="#ef4444" />,
    info:    <Info size={16} color="#60a5fa" />,
  };
  return map[status] || map.info;
};

/* ------------------------------------------------------------------ */
/*  Security Page                                                       */
/* ------------------------------------------------------------------ */
const Security = ({ t, config, setConfig, fetchConfig }) => {
  const [pending, setPending] = useState([]);
  const [approved, setApproved] = useState({});
  const [doctor, setDoctor] = useState(null);
  const [toast, setToast] = useState(null);
  const [revokeTarget, setRevokeTarget] = useState(null); // {channel, senderId}
  const [ownerChannelText, setOwnerChannelText] = useState('{}');
  const [ownerSaving, setOwnerSaving] = useState(false);

  const showToast = (msg, type = 'success') => setToast({ msg, type });

  // ---- Fetch data ----
  const fetchPairing = useCallback(async () => {
    const [pendRes, appRes] = await Promise.allSettled([
      axios.get(`${API_BASE}/pairing/pending`),
      axios.get(`${API_BASE}/pairing/approved`),
    ]);

    if (pendRes.status === 'fulfilled') {
      setPending(pendRes.value?.data?.pending || []);
    } else {
      console.error('Failed to load pending pairing data', pendRes.reason);
    }

    if (appRes.status === 'fulfilled') {
      setApproved(appRes.value?.data?.approved || {});
    } else {
      console.error('Failed to load approved pairing data', appRes.reason);
    }

    const failed = [pendRes, appRes].find((item) => item.status === 'rejected');
    if (failed && failed.status === 'rejected') {
      const detail = failed.reason?.response?.data?.detail;
      setToast({
        msg: detail ? `Failed to load pairing data: ${String(detail)}` : 'Failed to load pairing data',
        type: 'error',
      });
    }
  }, []);

  const fetchDoctor = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE}/health/doctor`);
      setDoctor(res.data);
    } catch (e) {
      console.error('Failed to load doctor data', e);
    }
  }, []);

  useEffect(() => {
    fetchPairing();
    fetchDoctor();
  }, [fetchPairing, fetchDoctor]);

  useEffect(() => {
    const ownerChannelIds = config?.security?.owner_channel_ids;
    if (ownerChannelIds && typeof ownerChannelIds === 'object' && !Array.isArray(ownerChannelIds)) {
      setOwnerChannelText(JSON.stringify(ownerChannelIds, null, 2));
    } else {
      setOwnerChannelText('{}');
    }
  }, [config]);

  const ownerChannelIdMap = useMemo(() => {
    const raw = config?.security?.owner_channel_ids;
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      return {};
    }
    return Object.fromEntries(
      Object.entries(raw)
        .map(([channel, senderId]) => [String(channel || '').trim(), String(senderId || '').trim()])
        .filter(([channel, senderId]) => channel && senderId)
    );
  }, [config]);

  const effectiveApproved = useMemo(() => {
    const merged = {};
    const addSender = (channel, senderId) => {
      const c = String(channel || '').trim();
      const s = String(senderId || '').trim();
      if (!c || !s) return;
      if (!merged[c]) merged[c] = new Set();
      merged[c].add(s);
    };

    if (approved && typeof approved === 'object' && !Array.isArray(approved)) {
      Object.entries(approved).forEach(([channel, senders]) => {
        if (!Array.isArray(senders)) return;
        senders.forEach((senderId) => addSender(channel, senderId));
      });
    }

    Object.entries(ownerChannelIdMap).forEach(([channel, senderId]) => addSender(channel, senderId));

    return Object.fromEntries(
      Object.entries(merged)
        .map(([channel, senders]) => [channel, Array.from(senders).sort()])
        .filter(([, senders]) => senders.length > 0)
    );
  }, [approved, ownerChannelIdMap]);

  // ---- Actions ----
  const handleApprove = async (code) => {
    try {
      await axios.post(`${API_BASE}/pairing/approve`, { code });
      showToast(t?.pairingApproved || 'Pairing approved');
      fetchPairing();
    } catch {
      showToast(t?.pairingFailed || 'Failed to approve', 'error');
    }
  };

  const handleReject = async (code) => {
    try {
      await axios.post(`${API_BASE}/pairing/reject`, { code });
      showToast(t?.pairingRejected || 'Pairing rejected');
      fetchPairing();
    } catch {
      showToast(t?.pairingFailed || 'Failed to reject', 'error');
    }
  };

  const handleRevoke = async (channel, senderId) => {
    try {
      await axios.post(`${API_BASE}/pairing/revoke`, { channel, sender_id: senderId });
      showToast(t?.pairingRevoked || 'Access revoked');
      fetchPairing();
    } catch {
      showToast(t?.pairingFailed || 'Failed to revoke', 'error');
    }
    setRevokeTarget(null);
  };

  const handleSaveOwnerChannels = async () => {
    const normalizedText = (ownerChannelText || '')
      .replace(/[“”]/g, '"')
      .replace(/[‘’]/g, "'")
      .replace(/，/g, ',')
      .replace(/：/g, ':');
    if (normalizedText !== ownerChannelText) {
      setOwnerChannelText(normalizedText);
    }

    let parsed;
    try {
      parsed = JSON.parse(normalizedText || '{}');
    } catch {
      showToast(t?.ownerChannelIdsInvalidJson || 'Owner Channel IDs must be valid JSON object', 'error');
      return;
    }

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      showToast(t?.ownerChannelIdsInvalidType || 'Owner Channel IDs must be a JSON object', 'error');
      return;
    }

    const normalized = Object.fromEntries(
      Object.entries(parsed)
        .map(([channel, senderId]) => [String(channel || '').trim(), String(senderId || '').trim()])
        .filter(([channel, senderId]) => channel && senderId)
    );

    setOwnerSaving(true);
    try {
      await axios.post(`${API_BASE}/config`, {
        security: {
          owner_channel_ids: normalized,
        },
      });
      showToast(t?.ownerSettingsSaved || t?.saved || 'Saved');
      if (fetchConfig) {
        await fetchConfig();
      } else if (setConfig) {
        setConfig((prev) => ({
          ...(prev || {}),
          security: {
            ...(prev?.security || {}),
            owner_channel_ids: normalized,
          },
        }));
      }
    } catch (err) {
      const detail = err?.response?.data?.detail;
      showToast(detail ? String(detail) : (t?.pairingFailed || 'Operation failed.'), 'error');
    } finally {
      setOwnerSaving(false);
    }
  };

  // btnStyle kept for action buttons with specific colors
  const btnStyle = (color) => ({
    display: 'inline-flex',
    alignItems: 'center',
    gap: 4,
    padding: '6px 14px',
    borderRadius: 8,
    border: 'none',
    cursor: 'pointer',
    fontWeight: 600,
    fontSize: 12,
    color: '#fff',
    background: color,
    transition: 'all 0.15s',
  });

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', width: '100%' }}>
      {/* Header */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
          <Shield size={28} color="#ef233c" />
          <h1 style={{ fontSize: 26, margin: 0, color: '#fff', fontWeight: 700 }}>
            {t?.securityTitle || 'Security & Access'}
          </h1>
        </div>
        <p style={{ color: '#888', fontSize: 14, margin: 0 }}>
          {t?.securityDesc || 'DM pairing management and system health diagnostics.'}
        </p>
      </div>

      {/* ---- Owner Channel IDs ---- */}
      <Card style={{ marginBottom: 24 }}>
        <h2 style={{ color: '#fff', fontSize: 18, margin: '0 0 12px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <Lock size={18} color="#60a5fa" />
          {t?.ownerChannelIdsTitle || 'Owner Channel IDs'}
        </h2>
        <p style={{ color: '#6b7280', marginTop: 0, marginBottom: 12, fontSize: 13 }}>
          {t?.ownerChannelIdsDesc || 'Map channel -> owner sender_id. Example: {"feishu":"ou_xxx"}'}
        </p>
        <textarea
          className="input"
          rows={5}
          value={ownerChannelText}
          onChange={(e) => setOwnerChannelText(e.target.value)}
          placeholder={t?.ownerChannelIdsPlaceholder || '{"feishu":"ou_xxx","telegram":"123456"}'}
        />
        <div style={{ marginTop: 10, display: 'flex', justifyContent: 'flex-end' }}>
          <button className="btn-primary" onClick={handleSaveOwnerChannels} disabled={ownerSaving}>
            <Shield size={14} style={{ marginRight: 6 }} />
            {ownerSaving ? (t?.saving || 'Saving...') : (t?.saveConfig || 'Save')}
          </button>
        </div>
      </Card>

      {/* ---- Pending Pairings ---- */}
      <Card style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ color: '#fff', fontSize: 18, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Lock size={18} color="#facc15" />
            {t?.pendingPairings || 'Pending Pairings'}
          </h2>
          <button onClick={fetchPairing} className="btn-ghost">
            <RefreshCw size={14} /> {t?.refresh || 'Refresh'}
          </button>
        </div>

        {pending.length === 0 ? (
          <p style={{ color: '#666', fontSize: 14 }}>{t?.noPending || 'No pending pairing requests.'}</p>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {pending.map((req) => (
              <div key={req.code} style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                padding: '12px 16px', background: 'rgba(250,204,21,0.06)', borderRadius: 12,
                border: '1px solid rgba(250,204,21,0.15)',
              }}>
                <div>
                  <span style={{ color: '#facc15', fontFamily: 'monospace', fontWeight: 700, fontSize: 16, marginRight: 12 }}>{req.code}</span>
                  <span style={{ color: '#aaa', fontSize: 13 }}>{req.channel} / {req.sender_id}</span>
                  <span style={{ color: '#666', fontSize: 12, marginLeft: 12 }}>expires in {req.expires_in}s</span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button onClick={() => handleApprove(req.code)} style={btnStyle('#22c55e')}>
                    <UserCheck size={14} style={{ marginRight: 4 }} /> Approve
                  </button>
                  <button onClick={() => handleReject(req.code)} style={btnStyle('#ef4444')}>
                    <UserX size={14} style={{ marginRight: 4 }} /> Reject
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      {/* ---- Approved Senders ---- */}
      <Card style={{ marginBottom: 24 }}>
        <h2 style={{ color: '#fff', fontSize: 18, margin: '0 0 16px', display: 'flex', alignItems: 'center', gap: 8 }}>
          <UserCheck size={18} color="#4ade80" />
          {t?.approvedSenders || 'Approved Senders'}
        </h2>

        {Object.keys(effectiveApproved).length === 0 ? (
          <p style={{ color: '#666', fontSize: 14 }}>{t?.noApproved || 'No approved senders.'}</p>
        ) : (
          Object.entries(effectiveApproved).map(([channel, senders]) => (
            <div key={channel} style={{ marginBottom: 16 }}>
              <h3 style={{ color: '#ef233c', fontSize: 14, marginBottom: 8, textTransform: 'uppercase', letterSpacing: 1 }}>{channel}</h3>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
                {senders.map((sid) => {
                  const isOwnerSender = ownerChannelIdMap[channel] === sid;
                  return (
                  <div key={sid} style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '6px 12px', background: 'rgba(74,222,128,0.08)', borderRadius: 8,
                    border: '1px solid rgba(74,222,128,0.15)', fontSize: 13,
                  }}>
                    <span style={{ color: '#ccc', fontFamily: 'monospace' }}>{sid}</span>
                    {isOwnerSender ? (
                      <span style={{ color: '#60a5fa', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.8 }}>
                        owner
                      </span>
                    ) : (
                      <button
                        onClick={() => setRevokeTarget({ channel, senderId: sid })}
                        style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#ef4444', padding: 2 }}
                        title="Revoke access"
                      >
                        <XCircle size={14} />
                      </button>
                    )}
                  </div>
                  );
                })}
              </div>
            </div>
          ))
        )}
      </Card>

      {/* ---- System Doctor ---- */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 16 }}>
          <h2 style={{ color: '#fff', fontSize: 18, margin: 0, display: 'flex', alignItems: 'center', gap: 8 }}>
            <Heart size={18} color="#f472b6" />
            {t?.doctorTitle || 'System Doctor'}
          </h2>
          <button onClick={fetchDoctor} className="btn-ghost">
            <RefreshCw size={14} /> {t?.refresh || 'Refresh'}
          </button>
        </div>

        {!doctor ? (
          <p style={{ color: '#666', fontSize: 14 }}>Loading...</p>
        ) : (
          <>
            {/* Overall status */}
            {(() => {
              const overall = doctor.overall ?? 'unknown';
              return (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 12, padding: '14px 20px',
                  background: overall === 'healthy'
                    ? 'rgba(74,222,128,0.08)'
                    : overall === 'degraded'
                    ? 'rgba(250,204,21,0.08)'
                    : 'rgba(239,68,68,0.08)',
                  borderRadius: 12, marginBottom: 16,
                  border: `1px solid ${
                    overall === 'healthy' ? 'rgba(74,222,128,0.2)' :
                    overall === 'degraded' ? 'rgba(250,204,21,0.2)' :
                    'rgba(239,68,68,0.2)'
                  }`,
                }}>
                  {overall === 'healthy'
                    ? <CheckCircle size={20} color="#4ade80" />
                    : overall === 'degraded'
                    ? <AlertTriangle size={20} color="#facc15" />
                    : <XCircle size={20} color="#ef4444" />
                  }
                  <div>
                    <span style={{ color: '#fff', fontWeight: 600, fontSize: 15 }}>
                      {overall.toUpperCase()}
                    </span>
                    <span style={{ color: '#888', fontSize: 13, marginLeft: 12 }}>
                      {doctor.warnings ?? 0} warning(s), {doctor.errors ?? 0} error(s)
                    </span>
                  </div>
                </div>
              );
            })()}

            {/* Check list */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {(doctor.checks ?? []).map((check, idx) => (
                <div key={idx} style={{
                  display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
                  background: 'rgba(0,0,0,0.15)', borderRadius: 10,
                }}>
                  <StatusIcon status={check.status} />
                  <span style={{ color: '#ccc', fontSize: 13, flex: 1 }}>
                    <strong style={{ color: '#fff' }}>{check.name}</strong> — {check.message}
                  </span>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 12, color: '#555', fontSize: 12 }}>
              {doctor.platform} / Python {doctor.python}
            </div>
          </>
        )}
      </Card>

      {/* Toast */}
      {toast && (
        <div style={{
          position: 'fixed', bottom: 32, right: 32, zIndex: 9999,
          padding: '14px 28px', borderRadius: 12,
          background: toast.type === 'error' ? 'rgba(239,68,68,0.85)' : 'rgba(34,197,94,0.85)',
          backdropFilter: 'blur(12px)', color: '#fff', fontWeight: 600, fontSize: 14,
          boxShadow: '0 8px 32px rgba(0,0,0,0.4)', animation: 'fadeIn 0.3s ease-out',
        }}
        onClick={() => setToast(null)}
        >
          {toast.msg}
        </div>
      )}

      <ConfirmModal
        open={!!revokeTarget}
        title="Revoke Access"
        message={revokeTarget ? `Revoke access for ${revokeTarget.senderId} on ${revokeTarget.channel}? They will need to re-pair.` : ''}
        confirmText="Revoke"
        onConfirm={() => revokeTarget && handleRevoke(revokeTarget.channel, revokeTarget.senderId)}
        onCancel={() => setRevokeTarget(null)}
      />
    </div>
  );
};

export default Security;
