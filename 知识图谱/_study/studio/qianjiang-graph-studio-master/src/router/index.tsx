import { lazy } from 'react'
// 如果你是js就直接无视这个interface的定义
interface Router {
  name?: string,
  path: string,
  children?: Array<Router>,
  component: any
}
// 如果你是js就直接无视这个: Array<Router>的类型限定
const router: Array<Router> = [
  {
    path: '',
    component: lazy(() => import(/* webpackChunkName: "graph" */ '@/views/chart-explore/ChartExplore'))
  },
  {
    path: 'schema',
    component: lazy(() => import(/* webpackChunkName: "schema" */'@/views/schema/Schema'))
  },
  {
    path: 'space-edit',
    component: lazy(() => import(/* webpackChunkName: "space-edit" */'@/views/space-edit/SpaceEdit'))
  }
]

export default router
