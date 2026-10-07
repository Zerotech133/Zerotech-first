import {
  ApartmentOutlined,
  BranchesOutlined,
  CopyrightCircleOutlined,
  DeploymentUnitOutlined,
  GatewayOutlined,
  TrademarkCircleOutlined,
} from '@ant-design/icons';
import {EGraphLayoutType} from '../../common';

// G6布局预览
// @see: https://antv-graphin.gitee.io/graphin/layout/network#%E5%B8%83%E5%B1%80%E9%A2%84%E8%A7%88
export const graphLayoutConfig = [
  // {
  //   title: '力导向布局-1',
  //   type: EGraphLayoutType['graphin-force'],
  //   icon: BranchesOutlined,
  // },
  {
    title: '力导向布局',
    type: EGraphLayoutType['force'],
    icon: BranchesOutlined,
  },
  {
    title: '网络布局',
    type: EGraphLayoutType['gForce'],
    icon: BranchesOutlined,
  },
  {
    title: '同心圆布局',
    type: EGraphLayoutType['concentric'],
    icon: DeploymentUnitOutlined,
  },
  {
    title: '圆形布局',
    type: EGraphLayoutType['circular'],
    icon: CopyrightCircleOutlined,
  },
  {
    title: '层次布局',
    type: EGraphLayoutType['dagre'],
    icon: ApartmentOutlined,
  },
  {
    title: '流程图布局',
    type: EGraphLayoutType['dagre'],
    icon: TrademarkCircleOutlined,
  },
  {
    title: '网格布局',
    type: EGraphLayoutType['grid'],
    icon: GatewayOutlined,
  },
  {
    title: '随机布局',
    type: EGraphLayoutType['random'],
    icon: TrademarkCircleOutlined,
  },
  {
    title: '集合布局',
    type: EGraphLayoutType['comboCombined'],
    icon: TrademarkCircleOutlined,
  }
];

const map = new Map()
graphLayoutConfig.forEach(item => {
  map.set(item.title,item.type)
})

export const GraphLayoutConfigMap = map