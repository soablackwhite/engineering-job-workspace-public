import { env } from 'cloudflare:workers';
import {ownerAuthorizationFailure} from '../lib/authorization';
export function authorizeRequest(request:Request) { return ownerAuthorizationFailure(request,env); }
export function getDb() { if (!env.DB) throw new Error('Database unavailable'); return env.DB; }

export function getDocuments() { if (!env.DOCUMENTS) throw new Error("Documents unavailable"); return env.DOCUMENTS; }
