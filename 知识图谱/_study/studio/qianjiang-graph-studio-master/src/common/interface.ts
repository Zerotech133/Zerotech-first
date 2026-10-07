type edgeTypes = 'account_call' | 'account_chat' | 'account_trade' | 'suspicion_account'

export type findNodeQuery = {
  keyWord: string,
  type?: string
}

export type extendNodeQuery = {
  edgeTypes: edgeTypes,
  direction: 'in' | 'out' | 'inOrOut',
  max: number,
  min: number,
  vids: string[]
}

export type findPathQuery = {
  edgeTypes: edgeTypes,
  direction: 'REVERSELY' | 'BIDIRECT',
  startVid: string,
  endVid: string,
  step: number,
  type: 'ALL' | 'SHORTEST' | 'NOLOOP',
  limit: number
}

export type findSubGraphQuery = {
  step: number,
  vidList: string[],
  showLeaf: boolean,
  threshold?: number
}

export type graphData = {
  libraryId?: string,
  name?: string,
  imageUrl: string,
  remark?: string,
  data: string,
  libraryName?: string
}
