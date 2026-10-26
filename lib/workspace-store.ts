import {getDb} from '@/db';
import {projectWorkspace,type WorkflowData} from './workspace';
import type {Tracking} from './crm';
import {readWorkflow} from './workflow-store';
export async function readWorkspace(){
 const [saved,row]=await Promise.all([
  getDb().prepare('SELECT id,status,notes,follow_up AS followUp,updated_at AS updatedAt FROM tracking').all<Tracking&{id:string}>(),
  readWorkflow()
 ]);
 return {...projectWorkspace(row.state as WorkflowData,saved.results),revision:row.revision};
}
