import { type } from "os"

export type extendNodeQuery = {
  edgeTypeNameList: string[],
  extendDirection: 'in' | 'out' | 'both',
  maxStep: number,
  minStep: number,
  startVertexId: string,
  timeCondition: {
    conditionType: string,
    value: string,
    value2?: string
  }
}
export type findPathQuery = {
  edgeTypeNameList: Array<string>,
  direction: 'REVERSELY' | 'BIDIRECT',
  startVertexId : string,
  endVertexId: string,
  maxStep: number,
  searchPathType: 'all' | 'shortest',
  total: number
}

export type DataRangeINF = {
  id: string,
  key: string,
  title: string,
  option: Array<any>
}