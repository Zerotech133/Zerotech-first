import { createStore } from "redux";
import { rootReducer } from '@/redux/reducer/index'

// 配置redux-toolkit工具，方便浏览器调试、查看redux数据
// @ts-ignore
const store = createStore(rootReducer, window.__REDUX_DEVTOOLS_EXTENSION__ && window.__REDUX_DEVTOOLS_EXTENSION__())

export default store
