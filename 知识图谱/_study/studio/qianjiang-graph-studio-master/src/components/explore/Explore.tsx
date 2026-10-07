import React, {useCallback, useContext, useEffect, useRef, useState} from 'react'
import {
  AutoComplete,
  Button,
  Col,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Row,
  Select,
  SelectProps,
  Spin,
  Tabs
} from 'antd'
import { searchPathType, pathDirections as directions} from "@/utils/common-data"
import {DebounceSelect} from "../"
import {debounce} from "lodash"
import { findNodeApi, findPathApi ,findById} from "@/api/chart"
import {GraphinContext} from "@antv/graphin";
import {IG6GraphEvent} from "@antv/g6";
import { dealNodes, dealEdges } from '@/utils'
import './Explore.scss'
// import { PlusSquareOutlined, MinusSquareOutlined } from '@ant-design/icons'
import {graphStrengths} from '@/common'
import {findSubGraphApi} from '@/api/chart'
import { EdgeTypeItem } from '@/views/chart-explore/form-items/FormItems'
import store from '@/redux/store/index'
import { setSearchNodes } from '@/redux/action'

const {TabPane} = Tabs
const {Option} = Select
type props = {
  visible: boolean,
  setVisible: (visible: boolean) => void,
  onExploreClose: () => void,
  mergeData: (data: any) => void,
  activeKey: string,
  edgeTypeNameList: any,
  iconPathList: any,
  historyData: any
}

export const Explore: React.FC<props> = ({visible, setVisible, onExploreClose, mergeData, activeKey, edgeTypeNameList,iconPathList, historyData}) => {
  const [startOptions, setStartOptions] = useState<SelectProps<object>['options']>()
  const [endOptions, setEndOptions] = useState<SelectProps<object>['options']>()
  const [startVertexId, setStartVertexId] = useState('')
  const [endVertexId, setEndVertexId] = useState('')
  const [vertexlist, setVertexList] = useState<any>([])
  const {graph} = useContext(GraphinContext)
  const lastNodeIdRef = useRef<string>('')
  const [pathLoading, setPathLoading] = useState(false)
  const [graphLoading, setGraphLoading] = useState(false)
  const [nativeActiveKey, setNativeActiveKey] = useState('path')
  const [selectorForm, setSelectorForm] = useState<any>(null)
  const [pathStartLoading, setPathStartLoading] = useState(false)
  const [pathEndLoading, setPathEndLoading] = useState(false)
  const [form] = Form.useForm();
  const [edgeParamList, setEdgeParamList] =useState<any>([])
  const graphRef = useRef<any>(null)
  const [form1] = Form.useForm();
  const [StartPointIcon, setStartPointIcon] = useState<string>('')//起点小图标
  const [endPointIcon, setEndPointIcon] = useState<string>('')//终点小图标
  const [startVertexData, setStartVertexData] = useState<any>()
  const [endVertexData, setEndVertexData] = useState<any>()
  const [taskName,setTaskName] = useState("")
  // 录入历史记录参数
  useEffect(() => {
    if (historyData) {
      const { type, param } = historyData
      // 查找路径
      if (type === 'findPath') {
        const { edgeTypeNameList, direction , startVertexId, endVertexId, searchPathType, maxStep, total, taskName } = JSON.parse(param)
        setStartOptions([])
        setEndOptions([])
        setStartVertexId(startVertexId.id)
        setEndVertexId(endVertexId.id)
        setStartVertexData(JSON.parse(startVertexId.name))
        setEndVertexData(JSON.parse(endVertexId.name))
        form.setFieldsValue({
          edgeTypeNameList,
          taskName,
          direction,
          startVertexId: JSON.parse(startVertexId.name).name,
          endVertexId: JSON.parse(endVertexId.name).name,
          searchPathType,
          maxStep,
          total: total || 5
        })
      } else { // 连通性分析
        const { vertexIdList, step , edgeParamList, taskName } = JSON.parse(param)
        let list=[]
        vertexIdList?.forEach((item: any)=> {
          let param={} as any
          if (item.id) {
            param={id:item.id}
          }else{
            param={id:item}
          }
          let name, iconPath
          findById(param).then((res: any) => {
            name=res.data.name
            iconPath='/api/file'+res.data.tag.iconPath
            list.push({name,iconPath,key:param.id})
          }).then((res:any)=>{
            if (list.length===vertexIdList.length) {
              const vertexIdListData = list?.map((list: any)=> {
               const {name,iconPath,key}=list
                return {
                  key:key,
                  label: (
                    <div  style={{display:"flex",alignItems: "center"}}>
                      {iconPath&&
                      <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPath} alt="" />
                      }
                      <span>{name}</span>
                    </div>
                  )
                }
              })
              setVertexList(vertexIdListData)
              form1.setFieldsValue({
                vertexIdList: vertexIdListData,
                step,
                taskName,
                list: edgeParamList
              })
            }
          })
        });
      }
    }
  }, [historyData, form, form1])

  useEffect(() => {
    const handleClick = (evt: IG6GraphEvent) => {
      if (evt.item === null) {
        return
      }
      lastNodeIdRef.current = evt.item.getID()
    }
    graph.on('node:click', handleClick)
    return () => {
      graph.off('node:click', handleClick)
    }
  }, [graph])
  useEffect(() => {
    if(historyData)return
    if (visible) {
      let time=(nativeActiveKey==="path"?"查找路径-":"连通性分析-")+(new Date()).toLocaleString()
      setTaskName(time)
      form.setFieldsValue({taskName:time})
      form1.setFieldsValue({taskName:time})
    }else{
      form.resetFields();
      form1.resetFields();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visible])
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleStartSearch = useCallback(debounce((value: string) => {
    if (!value) {
      setStartOptions([])
      return
    }
    setPathStartLoading(true)
    findNodeApi({keyword: value}).then((res: any) => {
      const iconPathList: any = store.getState().iconPathList
      setStartOptions(res.data.map((it: any) => {
        return {
          key: it.id,
          value: it.id,
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          name: it.name,
          attrs: {...it.propertyList},
          tag:it.tag
        }
      }))
      setPathStartLoading(false)
    }).catch(e => {
      setPathStartLoading(false)
    })
  }, 300), [])

  const onInputStartChange = (e: any) => {
    handleStartSearch(e.target.value)
  }

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleEndSearch = useCallback(debounce((value: string) => {
    if (!value) {
      setEndOptions([])
      return
    }
    setPathEndLoading(true)
    findNodeApi({keyword: value}).then((res: any) => {
      const iconPathList: any = store.getState().iconPathList
      setEndOptions(res.data.map((it: any) => {
        return {
          key: it.id,
          value: it.id,
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          name: it.name,
          attrs: {...it.propertyList},
          tag:it.tag
        }
      }))
      setPathEndLoading(false)
    }).catch(e => {
      setPathEndLoading(false)
    })
  }, 300), [])

  const onInputEndChange = (e: any) => {
    handleEndSearch(e.target.value)
  }

  const findNode = (value: string) => {
    return new Promise(((resolve, reject) => {
      if (!value) {
        setStartOptions([])
        setEndOptions([])
        reject()
      }
      findNodeApi({keyword: value}).then((res: any) => {
        const iconPathList: any = store.getState().iconPathList
        resolve(res.data.map((it: any) => ({
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          value: it.id
        })))
      }).catch(e => {
        message.error('接口请求错误')
        reject(e)
      })
    }))
  }

  const onStartSelect = (value: any, option: any) => {
    setStartOptions(startOptions!.filter(it => !it.id === option.key))
    setStartVertexId(option.key)
    form.setFieldsValue({
      startVertexId: option.name
    })
  }

  const onEndSelect = (value: any, option: any) => {
    setEndOptions(endOptions!.filter(it => !it.id === option.key))
    setEndVertexId(option.key)
    form.setFieldsValue({
      endVertexId: option.name
    })
  }

  const handleCancel = () => {
    setStartOptions([])
    setEndOptions([])
    onExploreClose()
  }
  // 查找路径
  function onPathFinish(values: any) {
    setPathLoading(true)
    if (values.edgeTypeNameList.includes('all')) {
      values.edgeTypeNameList = values.edgeTypeNameList.filter((item:any) => {return item!=='all'})
    }
    const param = {
      ...values,
      startVertexId: {id: startVertexId, name: JSON.stringify(startVertexData)},
      endVertexId: {id: endVertexId, name: JSON.stringify(endVertexData)}
    }
    findPathApi(param).then((res: any) => {
      if (!res.data) {
        message.warn('未查询到数据')
        setPathLoading(false)
        return
      }
      const {vertexList, pathList} = res.data
      if (vertexList.length===0 && pathList.length===0) {
        message.warn('未查询到数据')
        setPathLoading(false)
        return
      }
      store.dispatch(setSearchNodes({searchNodes: [startVertexId, endVertexId]}))
      let edgeList = [...pathList.flat()]
      let edges = dealEdges(edgeList)
      let nodes = dealNodes(vertexList)
      mergeData({
        highlight: true,
        edges: edges,
        nodes
      })
      setPathLoading(false)
      setVisible(false)
    }).catch(e => {
      message.error('接口请求错误')
      console.error(e)
      setPathLoading(false)
    })
  }
  // 是否是最大或者最小
  // const getSorting = (params: any, type: string) => {
  //   if (['最大', '最小'].includes(params.conditionType)) {
  //     let {conditionType, value} = params
  //     let sorting = {
  //       type: type,
  //       direction: conditionType === '最大' ? 'desc' : 'asc',
  //       limit: value
  //     }
  //     return sorting
  //   }
  //   return null
  // }
  // 连通性分析
  function onGraphFinish(values: any) {
    let {vertexIdList, list} = form1.getFieldsValue()
    vertexIdList = vertexlist?.map((item: any) => {return item.key})
    let params: any = {step: values.step, vertexIdList, edgeParamList: list, taskName:values.taskName }
    setGraphLoading(true)
    findSubGraphApi(params).then((res: any) => {
      if (!res.data) {
        message.warn('未查询到数据')
        setGraphLoading(false)
        return
      }
      setGraphLoading(false)
      setVisible(false)
      // 查询点保存
      const searchNodes = vertexIdList
      store.dispatch(setSearchNodes({searchNodes}))
      const {edgeList, vertexList} = res.data
      mergeData({
        highlight: true,
        edges: dealEdges(edgeList),
        nodes: dealNodes(vertexList)
      })
    }).catch(e => {
      message.error('接口请求错误')
      setGraphLoading(false)
    })
  }

  useEffect(() => {
    if (!visible || historyData) {
      return
    }
    if (activeKey === 'path') {
      const selectedNodes = graph.findAllByState('node', 'selected')
      if (selectedNodes.length === 1) {
        const {id, style, type, tagName}: any = selectedNodes[0].getModel()
        form.setFieldsValue({
          startVertexId: `${style.label.value}(${type})`
        })
        setStartVertexId(id)
        setStartPointIcon(tagName)
        setStartVertexData({iconPath: tagName, name: `${style.label.value}(${type})`})
      } else if (selectedNodes.length === 2) {
        let start: any = selectedNodes[0].getModel()
        let end: any = selectedNodes[1].getModel()
        if (end.id !== lastNodeIdRef.current) {
          [start, end] = [end, start]
        }
        setStartOptions([])
        setEndOptions([])
        form.setFieldsValue({
          startVertexId: `${start.style.label.value}(${start.type})`,
          endVertexId: `${end.style.label.value}(${end.type})`
        })
        setStartVertexId(start.id)
        setEndVertexId(end.id)
        setStartPointIcon(start.tagName)
        setEndPointIcon(end.tagName)
        setStartVertexData({iconPath: start.tagName, name: `${start.style.label.value}(${start.type})`})
        setEndVertexData({iconPath: end.tagName, name: `${end.style.label.value}(${end.type})`})
      }
    } else if (activeKey === 'graph') {
      if (!selectorForm) {
        return
      }
      const selectedNodes = graph.findAllByState('node', 'selected')
      let options: any[] = []
      let values: any[] = []
      selectedNodes.forEach((it: any) => {
        const {id, style, type}: any = it.getModel()
        options.push({
          label: `${style.label.value}(${type})`,
          value: id
        })
        values.push({
          label: `${style.label.value}(${type})`,
          key: id
        })
      })
      selectorForm.init(options, values)
      if (values.length)  setVertexList(values)
    }
  }, [visible, graph, activeKey, form, selectorForm, historyData])

  useEffect(() => {
    setNativeActiveKey(activeKey)
  }, [activeKey])

  const handleActiveChange = useCallback((activeKey: string) => {
    setNativeActiveKey(activeKey)
    let time=(activeKey==="path"?"查找路径-":"连通性分析-")+(new Date()).toLocaleString()
    if (activeKey==="path"&&!form.getFieldsValue().taskName?.length) {
      form.setFieldsValue({taskName:time})
      return
    }
    if (activeKey!=="path"&&!form1.getFieldsValue().taskName?.length) {
      form1.setFieldsValue({taskName:time})
      return 
    }
    setTaskName(time)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // select下拉多选
  function handleEdgeChange(value:any,option:any) {
    form.setFieldsValue({
      edgeTypeNameList: value
    })
  }

  // 子图edgeParamList初始化
  useEffect(() => {
    let list = [{
      edgeTypeName: null,
      strengthCondition: {
        conditionType: null,
        value: ''
      },
      countCondition: {
        conditionType: null,
        value: ''
      }
    }]
    setEdgeParamList(list)
  },[setEdgeParamList])
  if (!visible) {
    return null
  }
  // 查找路径起点对应的图标
  const onStartPointChange = (e:any,input:any) => {
    setStartPointIcon(input?.tag?.name)
    setStartVertexData({iconpPath: input?.tag?.name, name: input.name})
  }
   // 查找路径起点对应的图标
  const onEndPointChange = (e:any,input:any) => {
    setEndPointIcon(input?.tag?.name)
    setEndVertexData({iconpPath: input?.tag?.name, name: input.name})
  }
  const handleChange = (values: any) => {
    setVertexList(values)
  }

  return (
    <Modal maskClosable={false} title="探索" className="explore" visible={visible} onCancel={handleCancel} width={800} footer={null}>
      <Tabs defaultActiveKey="graph" activeKey={nativeActiveKey} onChange={handleActiveChange}>
        <TabPane tab="查找路径" key="path">
          <Form
            layout="vertical"
            form={form}
            onFinish={onPathFinish}
          >
            <Row gutter={30}>
              <Col span={12}>
                <Form.Item
                  label="任务名称"
                  name="taskName"
                  initialValue={taskName}
                  rules={[{ required: true, message: '请输入任务名称' }]}
                >
                  <Input style={{ width: '100%' }} placeholder="请输入任务名称" />
                </Form.Item>
              </Col>
              <Col span={12}>
                {/* 边类型 */}
                <EdgeTypeItem edgeTypeNameList={edgeTypeNameList} handleEdgeChange={handleEdgeChange}/>
              </Col>
            </Row>
            <Row gutter={30}>
              <Col span={12}>
                <Form.Item label="起点" name="startVertexId" rules={[{required: true, message: '请选择起点'}]}>
                  <AutoComplete
                    dropdownMatchSelectWidth={true}
                    style={{
                      width: '100%'
                    }}
                    notFoundContent={pathStartLoading ? <div style={{textAlign: 'center', padding: 30}}><Spin/></div> :
                      <Empty description='暂无数据' image={Empty.PRESENTED_IMAGE_SIMPLE}/>}
                    backfill={false}
                    options={startOptions}
                    onSelect={onStartSelect}
                    onChange={onStartPointChange}
                  >
                    <Input.Search
                        placeholder="请选择起点"
                        onSearch={handleStartSearch}
                        onChange={onInputStartChange}
                        prefix={<img style={{
                          width:"15px",
                          height:"15px",
                          marginRight: "5px",
                          display:iconPathList[StartPointIcon]?"block":"none"
                        }}
                          src={iconPathList[StartPointIcon]} alt=""
                          />}
                      />
                  </AutoComplete>
                </Form.Item>
              </Col>

              <Col span={12}>
                <Form.Item label="终点" name="endVertexId" rules={[{required: true, message: '请选择终点'}]}>
                  <AutoComplete
                    dropdownMatchSelectWidth={true}
                    style={{
                      width: '100%'
                    }}
                    notFoundContent={pathEndLoading ? <div style={{textAlign: 'center', padding: 30}}><Spin/></div> :
                      <Empty description='暂无数据' image={Empty.PRESENTED_IMAGE_SIMPLE}/>}
                    backfill={false}
                    options={endOptions}
                    onSelect={onEndSelect}
                    onChange={onEndPointChange}
                  >
                    <Input.Search
                        placeholder="请选择终点"
                        onSearch={handleEndSearch}
                        onChange={onInputEndChange}
                        prefix={<img style={{
                          width:"15px",
                          height:"15px",
                          marginRight: "5px",
                          display:iconPathList[endPointIcon]?"block":"none"
                        }}
                          src={iconPathList[endPointIcon]} alt=""
                          />}
                        />
                  </AutoComplete>
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={30}>
              <Col span={12}>
                <Form.Item label="搜索类型" name="searchPathType" rules={[{required: true, message: '请选择搜索类型'}]}>
                  <Select
                    style={{width: '100%'}}
                    placeholder="请选择搜索类型"
                  >
                    {
                      searchPathType.map(it => (
                        <Option key={it.value} value={it.value}>{it.label}</Option>
                      ))
                    }
                  </Select>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="步数" name="maxStep" rules={[{required: true, message: '请输入步数'}]}>
                  <InputNumber style={{width: '100%'}} min={1} placeholder="请输入步数"/>
                </Form.Item>
              </Col>
            </Row>
            <Row gutter={30}>
            <Col span={12}>
                <Form.Item label="方向" name="direction" rules={[{required: true, message: '请选择方向'}]}>
                  <Select
                    style={{width: '100%'}}
                    placeholder="请选择方向"
                  >
                    {
                      directions.map(it => (
                        <Option key={it.value} value={it.value}>{it.label}</Option>
                      ))
                    }
                  </Select>
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item label="结果数量" name="total" initialValue={20}>
                  <InputNumber style={{width: '100%'}} min={1} placeholder="请输入结果数量"/>
                </Form.Item>
                <br/>
              </Col>
            </Row>
            <Form.Item>
              <Button style={{width: '100%'}} type="primary" htmlType="submit" loading={pathLoading}>查找</Button>
              { pathLoading &&<div style={{color:"red", textAlign:'center', fontSize:"12px", marginTop:"6px"}}>若当前功能运行时间较长，可关闭页面，后续进入分析记录页面查看运行结果</div>}
            </Form.Item>
          </Form>
        </TabPane>
        <TabPane tab="连通性分析" key="graph">
          <Form
            ref={graphRef}
            layout="vertical"
            form={form1}
            onFinish={onGraphFinish}
          >
            <Form.Item
              label="任务名称"
              name="taskName"
              initialValue={ taskName }
              rules={[{ required: true, message: '请输入任务名称' }]}
            >
              <Input style={{ width: '100%' }} placeholder="请输入任务名称" />
            </Form.Item>
            <Form.Item
              className='redStart'
              label="点列表(请至少选择2个点列表)" name="vertexIdList"
              rules={[({ getFieldValue }) => ({
                validator(_, value) {
                    if (vertexlist?.length > 1) {
                      return Promise.resolve()
                    }
                    return Promise.reject(new Error('请至少选择2个点列表!'));
                  }
                })]}>
              <DebounceSelect
                ref={setSelectorForm}
                mode="multiple"
                allowClear={true}
                placeholder="请输入关键字"
                fetchOptions={findNode}
                style={{
                  width: '100%',
                }}
                vertexlist={vertexlist}
                onChange={handleChange}
              />
            </Form.Item>
            <Form.Item label="步数" name="step" rules={[{required: true, message: '请输入步数'}]}>
              <InputNumber style={{width: '100%'}} min={1} placeholder="请输入步数"/>
            </Form.Item>
            {/* 边类型 强度 次数 */}
            <div style={{maxHeight: '300px',overflowY: 'scroll'}}>
            {
              edgeParamList.map((item: any,index: number) =>(
                <div  className='form-group' key={index}>
                  <Form.Item label="类型"
                    className='redStart'
                    name={['list',index,'edgeTypeName']}
                    rules={[{required:true, message: '请选择类型'}]}>
                    <Select
                      placeholder="请选择"
                      allowClear={true}
                      >
                        {
                          edgeTypeNameList.map((it: any) => (
                            <Option key={it.id} value={it.name}>{it.name}</Option>
                          ))
                        }
                      </Select>
                  </Form.Item>
                  <Form.Item label="强度">
                    <Form.Item name={['list',index, 'strengthCondition', 'conditionType']}>
                      <Select
                      placeholder="请选择"
                      allowClear={true}
                      >
                      {
                        graphStrengths.map((it: any) => (
                          <Option key={it.value} value={it.value} disabled={(['最大','最小'].includes(item.countCondition.conditionType))&&(['最大','最小'].includes(it.value))}>{it.label}</Option>
                        ))
                      }
                      </Select>
                    </Form.Item>
                    <Form.Item name={['list',index, 'strengthCondition', 'value']}>
                      <InputNumber min={1} placeholder="请输入" />
                    </Form.Item>
                  </Form.Item>
                  <Form.Item label="次数">
                    <Form.Item name={['list',index, 'countCondition', 'conditionType']}>
                      <Select
                      placeholder="请选择"
                      allowClear={true}
                      >{
                        graphStrengths.map((it: any) => (
                          <Option key={it.value} value={it.value} disabled={(['最大','最小'].includes(item.strengthCondition.conditionType))&&(['最大','最小'].includes(it.value))}>{it.label}</Option>
                        ))
                      }
                      </Select>
                    </Form.Item>
                    <Form.Item name={['list',index, 'countCondition', 'value']}>
                      <InputNumber min={1} placeholder="请输入" />
                    </Form.Item>
                  </Form.Item>
                  {/* 此功能暂时隐藏 */}
                  {/* <div className="end-icon">
                    {edgeParamList.length-1 === index && <PlusSquareOutlined onClick={addForm}/>}
                    {edgeParamList.length > 1 && <MinusSquareOutlined onClick={() => deleteForm(index)}/>}
                  </div> */}
                </div>
              ))
            }
            </div>
            <Form.Item>
              <Button style={{width: '100%'}} type="primary" htmlType="submit" loading={graphLoading}>查找</Button>
              { graphLoading &&<div style={{color:"red", textAlign:'center', fontSize:"12px", marginTop:"6px"}}>若当前功能运行时间较长，可关闭页面，后续进入分析记录页面查看运行结果</div>}
            </Form.Item>
          </Form>
        </TabPane>
      </Tabs>
    </Modal>
  )
}
