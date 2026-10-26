import {env} from 'cloudflare:workers';
import {getDb,getDocuments} from '@/db';
import {emptyWorkflow,validateState} from './workflow';
import {readStoredWorkflow,saveStoredWorkflow} from './workflow-storage-core';
export function readWorkflow(){return readStoredWorkflow(getDb(),getDocuments(),emptyWorkflow);}
export function saveWorkflow(revision:number,state:any){return saveStoredWorkflow(getDb(),getDocuments(),revision,state,emptyWorkflow,(next,previous)=>validateState(next,previous,env.JOB_PIPELINE_LIVE_ENABLED==='supervised'));}
