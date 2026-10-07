const initState = {
  value: "",
  spaceId: "",
  nodeType: [],
  edgeType: [],
  iconPathList: {},
  currentData: {},
  searchNodes: [], // 分析的nodes参数
  dataIds: [],
  rawDataVisible: false,
  nodesData: [],
  edgesData: [],
  cardData: { cardKey:'', cardValueList: [] },
  hideMapList: []
}
const rootReducer = (state = initState, action) => {
  let type = action.type
  switch (type) {
    case "spaceId":
      state.spaceId = action.key
      state.value = action.value
      return state
    case "setDataIds":
      state.dataIds = action.dataIds
      return state
    case "setRawDataVisible":
      state.rawDataVisible = action.visible
      return state
    case "setGraphData":
      state.nodesData = action.nodesData
      state.edgesData = action.edgesData
      return state
    case "nodeType":
      state.nodeType= action.nodeType
      return state
    case "hideMapList":
      state.hideMapList= action.hideMapList
      return state
    default:
      state[type] = action[type]
      return state
  }
}

export { rootReducer }
