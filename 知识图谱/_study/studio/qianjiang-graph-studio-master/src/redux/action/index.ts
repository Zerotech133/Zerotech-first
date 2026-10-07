const setSpaceData = (spaceData: any) => {
  spaceData.spaceId = spaceData.key
  return { type: 'spaceId', ...spaceData }
}
const setNodeType = (data: object) => {
  return { type: 'nodeType', ...data }
}
const setEdgeType = (data: object) => {
  return { type: 'edgeType', ...data }
}
const setIconPath = (data: object) => {
  return { type: 'iconPathList', ...data }
}
const setCurrentData = (data: object) => {
  return { type: 'currentData', ...data }
}
const setSearchNodes = (data: object) => {
  return { type: 'searchNodes', ...data }
}
const setDataIds = (data: any) => {
  return { type: 'setDataIds', ...data }
}
const setRawDataVisible = (data: object) => {
  return { type: 'setRawDataVisible', ...data }
}
const setGraphData = (data: object) => {
  return { type: 'setGraphData', ...data }
}
const setCardData = (data: object) => {
  return { type: 'cardData', ...data }
}
const setHideMapList = (data: object) => {
  return { type: 'hideMapList', ...data }
}

export {
  setSpaceData,
  setNodeType,
  setEdgeType,
  setIconPath,
  setCurrentData,
  setSearchNodes,
  setDataIds,
  setRawDataVisible,
  setGraphData,
  setCardData,
  setHideMapList
}
