import React, { useContext, useEffect, useImperativeHandle, useRef, useState } from 'react'
import styles from "./Panel.module.scss"
import classNames from 'classnames'
import { Button, Empty, Form, InputNumber, message, Select, Table, Tooltip, Modal  } from 'antd'
import { IG6GraphEvent } from "@antv/g6"
import { GraphinContext } from "@antv/graphin"
import { directions } from "../../common"
import { extendNodeApi } from '../../api/chart'
import { StatisticsPanel } from './statistics-panel'
import { dealNodes, dealEdges, getSorting } from '@/utils'
import { ProcessLibraryList } from '../process-library-list'
import { StrengthCount } from '@/views/chart-explore/form-items/FormItems'
import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'
import { HistoryRecord } from './history-record/HistoryRecord'
import { AnalysisRecord } from './analysis-record/AnalysisRecord'
const { Option } = Select


type props = {
  activeId: string,
  num: number,
  saveGraphNum: number,
  mergeData: (data: any) => void,
  edgeTypeNameList: any,
  data: any,
  totalGraphData: any,
  hideMap: any,
  handleUpdateFinish: () => void,
  showDefaultPanel: (item: any) => void
} & {
  ref: any
}

const Panel: React.FC<props> = React.forwardRef(({
  activeId,
  num,
  saveGraphNum,
  mergeData,
  edgeTypeNameList,
  data,
  totalGraphData,
  handleUpdateFinish,
  showDefaultPanel,
  hideMap
}, ref) => {
  const [detailInfoVisible, setDetailInfoVisible] = useState(false)//详细信息面板是否显示
  const [libraryVisible, setLibraryVisible] = useState(false) // 过程库面板是否显示
  const [expandVisible, setExpandVisible] = useState(false) // 拓展面板是否显示statistics
  const [statisticsVisible, setStatisticsVisible] = useState(false) // 统计面板是否显示
  const [attrs, setAttrs] = useState<{ [key: string]: any } | null>(null)
  const [caseInfoList, setCaseInfoList] = useState<[]|null>(null)
  const lastClickElRef = useRef<any>(null)
  const { graph } = useContext(GraphinContext)
  const [loading, setLoading] = useState(false)
  const [form] = Form.useForm();
  const [totalData, setTotalData] = useState<any>()
  const expandRef = useRef<any>(null)
  const [historyRecordVisible, setHistoryRecordVisible] = useState(false) // 历史记录面板是否显示
  const [analysisRecordVisible, setAnalysisRecordVisible] = useState(false) // 分析记录面板是否显示
  const columns = [
    {
      title: '案件名称',
      dataIndex: 'caseName',
    },
    {
      title: '创建时间',
      dataIndex: 'createTime',
    }
  ]
  const [expandParam, setExpandParam] = useState<any>({})
  const [tooltip, setTooltip] = useState<boolean>(false)
  useImperativeHandle(ref, () => ({
    toggleLeft: (visible: boolean) => {
      setDetailInfoVisible(visible)
    },
    toggleRight: (visible: boolean) => {
      setExpandVisible(visible)
    }
  }), [])

  useEffect(() => {
    // 打开更新样式面板，关闭panel
    setExpandVisible(false)
    setStatisticsVisible(false)
    setHistoryRecordVisible(false)
    // 关闭分析记录
    setAnalysisRecordVisible(false)
  }, [num])

  useEffect(() => {
    const handleClick = (evt: IG6GraphEvent) => {
      if (evt.code) return
      if (evt.item === null || !evt.item) return
      if (evt.originalEvent && (evt.originalEvent as MouseEvent).ctrlKey) return
      const model = evt.item.getModel()
      if (!model.attrs || model.id.includes('gatherNodeId')) return
      let attrList = JSON.parse(JSON.stringify(model.attrs))
      if ('caseInfoList' in attrList) {
        attrList.caseInfoList=JSON.parse(attrList.caseInfoList)
        attrList.caseInfoList?.forEach((info:any) => {
          info.key=info.caseId
          info.createTime=new Date(info.createTime).toLocaleString()
        });
        setCaseInfoList(attrList.caseInfoList)
        delete (attrList.caseInfoList)
      }else{
        setCaseInfoList(null)
      }
      delete (attrList.suspectFlag)
      delete (attrList.suspectFlag1)
      setAttrs(attrList)
      lastClickElRef.current = evt.item
      // graph.setItemState(evt.item,'selected',true)
    }
    graph.on('node:click', handleClick)
    graph.on('edge:click', handleClick)
    graph.on('canvas:click', handleClick)
    graph.on('keyup', handleClick)
    return () => {
      graph.off('node:click', handleClick)
      graph.off('edge:click', handleClick)
      graph.off('canvas:click', handleClick)
      graph.off('keyup', handleClick)
    }
  }, [graph])

  // 选中详细信息按钮
  function showDetailInfo() {
    setDetailInfoVisible(!detailInfoVisible)
    if (libraryVisible) {
      setLibraryVisible(false)
    }
  }
  // 选中图谱列表按钮
  function showLibrary() {
    setLibraryVisible(!libraryVisible)
    if (detailInfoVisible) {
      setDetailInfoVisible(false)
    }
  }
  // 选中拓展按钮
  function showExpandInfo() {
    setExpandVisible(!expandVisible)
    // 关闭更新样式面板
    handleUpdateFinish()
    // 关闭统计
    setStatisticsVisible(false)
    // 关闭历史记录
    setHistoryRecordVisible(false)
    // 关闭分析记录
    setAnalysisRecordVisible(false)
  }
  // 选中统计按钮
  function showStatistics() {
    setStatisticsVisible(!statisticsVisible)
    // 关闭更新样式面板
    handleUpdateFinish()
    // 关闭拓展
    setExpandVisible(false)
    // 关闭历史记录
    setHistoryRecordVisible(false)
    // 关闭分析记录
    setAnalysisRecordVisible(false)
  }
  // 选择历史记录按钮
  function showHistoryRecord() {
    setHistoryRecordVisible(!historyRecordVisible)
    // 关闭更新样式面板
    handleUpdateFinish()
    // 关闭拓展
    setExpandVisible(false)
    // 关闭统计
    setStatisticsVisible(false)
    // 关闭分析记录
    setAnalysisRecordVisible(false)
  }
   // 选择分析记录按钮
   function showanAlysisRecord() {
    setAnalysisRecordVisible(!analysisRecordVisible)
    setHistoryRecordVisible(false)
    // 关闭更新样式面板
    handleUpdateFinish()
    // 关闭拓展
    setExpandVisible(false)
    // 关闭统计
    setStatisticsVisible(false)
  }
  // 拓展查询
  function onFinish(values: any) {
    const activeIdListSlected = graph.findAllByState('node', 'selected').map(it => it.getID())
    const activeIdListObvious = graph.findAllByState('node', 'obvious').map(it => it.getID())
    const activeIdList = [...activeIdListSlected, ...activeIdListObvious]
    if (activeIdList.length === 0) {
      message.warn('请选择要扩展的节点')
      return
    }
    if (values.edgeTypeNameList.includes('all')) {
      values.edgeTypeNameList = values.edgeTypeNameList.filter((item: any) => { return item !== 'all' })
    }

    let { strengthCondition, countCondition } = values
    let sorting = getSorting(strengthCondition, 'strength') || getSorting(countCondition, 'count')
    if (sorting) {
      values.sorting = sorting
    }
    store.dispatch(setSearchNodes({ searchNodes: activeIdList }))
    let param = {
      ...values,
      startVertexIdList: activeIdList
    }
    setExpandParam(param)
    if (!strengthCondition.value&&!countCondition.value) {
      setTooltip(true)
    } else {
      handleOk(param)
    }
  }
  function handleOk(param=undefined) {
    setTooltip(false)
    setLoading(true)
    const params = param?.strengthCondition ? param : expandParam
    extendNodeApi(params).then((res: any) => {
      if (!res.data) {
        message.warn('未查询到扩展数据')
        return
      }
      const { edgeList, vertexList } = res.data
      if (!edgeList.length && !vertexList.length) {
        message.warn('未查询到扩展数据')
        return
      }
      mergeData({
        edges: dealEdges(edgeList),
        nodes: dealNodes(vertexList),
        highlight: true
      })
    })
    .finally(() => {
      setLoading(false)
    })
  }
  function handleCancel() {
    setTooltip(false)
  }
  // select下拉多选
  function handleEdgeChange(value: any) {
    form.setFieldsValue({
      edgeTypeNameList: value
    })
  }

  useEffect(() => {
    setTotalData({
      strengthType: '',
      countType: ''
    })
  }, [setTotalData])
  // 筛选强度
  const changeStrength = (value: string) => {
    expandRef.current.validateFields([['strengthCondition', 'value']])
    setTotalData({
      ...totalData,
      strengthType: value
    })
  }
  const changeStrengthValue = () => {
    expandRef.current.validateFields([['strengthCondition', 'conditionType']])
  }
  // 筛选次数
  const changeCount = (value: string) => {
    expandRef.current.validateFields([['countCondition', 'value']])
    setTotalData({
      ...totalData,
      countType: value
    })
  }
  const changeCountValue = () => {
    expandRef.current.validateFields([['countCondition', 'conditionType']])
  }

  return (
    <>
      {/* 左侧面板 */}
      <div tabIndex={-1} className={styles['left-panel']} style={{ width: (detailInfoVisible || libraryVisible) ? '250px' : '20px' }}>
        {/* 详细信息面板 */}
        {
          detailInfoVisible &&
          <div className='detailInfo' style={{ width: '250px', padding: '15px' }}>
            {
              attrs ?
                <div>
                  {
                    Object.keys(attrs).map((item: any) => {
                      return (
                        <div key={item} className={styles['text']}>
                          {item}: <Tooltip title={attrs[item] ? attrs[item] : '暂无数据'}>
                            <span>{attrs[item] ? attrs[item].toString().slice(0, 20) : '暂无数据'}</span>
                          </Tooltip>
                        </div>
                      )
                    })
                  }
                  {
                    caseInfoList?
                    <Table
                      dataSource={caseInfoList}
                      bordered
                      size="small"
                      pagination={false}
                      scroll={{ y: 200 }}
                      columns={columns}
                      style={{marginTop:"12px"}}
                    >
                    </Table>
                    :<></>
                  }
                </div>


                : <div className={styles['no-data']}><Empty description='暂无数据' image={Empty.PRESENTED_IMAGE_SIMPLE} />
                </div>
            }
          </div>
        }
        {/* 图谱列表 */}
        {
          libraryVisible &&
          <div style={{ width: '250px', padding: '15px' }}>
            <ProcessLibraryList saveGraphNum={saveGraphNum}></ProcessLibraryList>
          </div>
        }
        {/* 左侧详细信息和图谱列表按钮 */}
        {
          <div className={styles['left-buttons']}>
            <div className={classNames(styles['detail-icon'], detailInfoVisible && styles['detail-icon-click'])} onClick={showDetailInfo}></div>
            <div className={styles['detail-text']} onClick={showDetailInfo}>详细信息</div>
            <div className={classNames(styles['process-icon'], libraryVisible && styles['process-icon-click'])} onClick={showLibrary}></div>
            <div className={styles['process-text']} onClick={showLibrary}>图谱列表</div>
          </div>
        }
      </div>
      <div tabIndex={-1} className={styles['right-panel']} style={{ width: expandVisible ? '300px' : (statisticsVisible || historyRecordVisible  ? '500px' : analysisRecordVisible ? "600px" : '20px') }}>
        {
          <div className={styles['right-buttons']}>
            <div className={classNames(styles['right-icon'], styles['expand-icon'], expandVisible && styles['right-icon-click'])} onClick={showExpandInfo}></div>
            <div className={classNames(styles['right-text'], styles['expand-text'])} onClick={showExpandInfo}>拓展</div>
            <div className={classNames(styles['right-icon'], styles['statistics-icon'], statisticsVisible && styles['right-icon-click'])} onClick={showStatistics}></div>
            <div className={classNames(styles['right-text'], styles['statistics-text'])} onClick={showStatistics}>统计</div>
            <div className={classNames(styles['right-icon'], styles['history-icon'], historyRecordVisible && styles['right-icon-click'])} onClick={showHistoryRecord}></div>
            <div className={classNames(styles['right-text'], styles['history-text'])} onClick={showHistoryRecord}>历史记录</div>
            <div className={classNames(styles['right-icon'], styles['analysis-icon'], analysisRecordVisible && styles['right-icon-click'])} onClick={showanAlysisRecord}></div>
            <div className={classNames(styles['right-text'], styles['analysis-text'])} onClick={showanAlysisRecord}>分析记录</div>
          </div>
        }
        {expandVisible &&
          <Form
            layout="vertical"
            onFinish={onFinish}
            form={form}
            ref={expandRef}
            style={{ padding: '20px' }}
            className={classNames(styles['expand-panel'])}
          >
            {/* 边类型 */}
            <EdgeTypeItem edgeTypeNameList={edgeTypeNameList} handleEdgeChange={handleEdgeChange} />

            <Form.Item label="方向" name="extendDirection" rules={[{ required: true, message: '请选择方向' }]}>
              <Select
                style={{ width: '100%' }}
                placeholder="请选择方向"
              >
                {
                  directions.map(it => (
                    <Option key={it.value} value={it.value}>{it.label}</Option>
                  ))
                }
              </Select>
            </Form.Item>
            <Form.Item label="最小步数" name="minStep" rules={[{ required: true, message: '请输入最小步数' }]}>
              <InputNumber style={{ width: '100%' }} min={1} />
            </Form.Item>
            <Form.Item label="最大步数" name="maxStep"
              rules={[{ required: true, message: '请输入最大步数' }, ({ getFieldValue }) => ({
                validator(_, value) {
                  const stepStart = getFieldValue('stepStart')
                  if (stepStart && value < stepStart) {
                    return Promise.reject(new Error('最小步数不能大于最大步数'))
                  }
                  return Promise.resolve()
                }
              })]}>
              <InputNumber style={{ width: '100%' }} min={1} />
            </Form.Item>
            <StrengthCount
              type={[]}
              strengthType={totalData.strengthType}
              countType={totalData.countType}
              changeStrength={changeStrength}
              changeCount={changeCount}
              changeStrengthValue={changeStrengthValue}
              changeCountValue={changeCountValue}
            />
            <br />
            <Form.Item>
              <Button style={{ width: '100%' }} type="primary" htmlType="submit" loading={loading}>
                拓展
              </Button>
            </Form.Item>
          </Form>
        }
        <Modal visible={tooltip} onOk={handleOk} onCancel={handleCancel}>
            <div>您尚未设置强度或次数的过滤条件，可能因扩展数量过多导致浏览器崩溃</div>
            <div>名词解释：</div>
            <div>强度：指关系总量，如交易关系的交易总金额，通话关系的通话时长</div>
            <div>次数：指关系发生的次数，如交易次数、通话次数等</div>
        </Modal>
        <StatisticsPanel
        visible={statisticsVisible}
        edgeTypeNameList={edgeTypeNameList}
        data={totalGraphData}
        hideMap={hideMap}
        mergeData={mergeData}
         />
        <HistoryRecord
          visible={historyRecordVisible}
          edgeTypeNameList={edgeTypeNameList}
          data={data}
          showDefaultPanel={showDefaultPanel}
        />
        <AnalysisRecord
          visible={analysisRecordVisible}
          mergeData={mergeData}
        />
      </div>
    </>
  )
})
Panel.displayName = 'Panel'
export { Panel }
