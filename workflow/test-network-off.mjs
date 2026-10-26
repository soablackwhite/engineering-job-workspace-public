// Imported into every Node test process; no external service is needed.
import net from 'node:net';
import tls from 'node:tls';
const blocked = () => { throw new Error('Network disabled during workflow tests'); };
net.Socket.prototype.connect = blocked;
tls.connect = blocked;
globalThis.fetch = blocked;
