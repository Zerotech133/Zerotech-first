import React, { useContext, useEffect, useState} from 'react'
import {AutoComplete, Empty, Input, Select, SelectProps, Spin} from 'antd'
import {GraphinContext, GraphinData} from '@antv/graphin'
import {findNodeApi} from "../../api/chart"
import store from '@/redux/store/index'

import './SearchPanel.scss'
type props = {
  data: GraphinData,
  mergeData: (data: any) => void,
  visible: boolean,
  spaceTagList: any,
  iconPathList: any,
  historyData: any
}

export const SearchPanel: React.FC<props> = ({data, mergeData, visible, spaceTagList, iconPathList,historyData}) => {
  const [options, setOptions] = useState<SelectProps<object>['options']>()
  const [tagId, setTagId] = useState<string>('')
  const [value, setValue] = useState<string>('')
  const graphin = useContext(GraphinContext)
  const [icon, setIcon] = useState<string>('')
  const {graph} = graphin
  const [loading, setLoading] = useState(false)

  // 录入历史记录参数
  useEffect(() => {
    if (historyData && historyData?.type === 'general') {
      const { tagName, keyword } = JSON.parse(historyData.param)
      setTagId(tagName)
      setValue(keyword)
    }
  }, [historyData])

  // eslint-disable-next-line react-hooks/exhaustive-deps
  const handleSearch = (value: string) => {
    getNodeList(value, tagId)
  }
  const getNodeList = (keyword: string, tagName: string) => {
    if (!keyword) {
      setOptions([])
      return
    }
    setLoading(true)
    findNodeApi({keyword, tagName}).then((res: any) => {
      const iconPathList: any = store.getState().iconPathList
      setOptions(res.data.map((it: any) => {
        return {
          key: it.id,
          value: it.id,
          label: (
            <div  style={{display:"flex",alignItems: "center"}}>
              {iconPathList[it.tag?.name]&&
              <img style={{width:"15px",height:"15px",marginRight: "5px"}} src={iconPathList[it.tag.name]} alt="" />
              }
              <span>{it.name}</span>
            </div>
          ),
          name: it.name,
          attrs: { ...it.propertyList },
          tag: it.tag
        }
      }))
      setLoading(false)
    })
  }

  const onSelect = (value: any, option: any) => {
    setValue(option.name)
    // 撤回操作后,图data换成栈里面的数据
    // let currentData: any = store.getState().currentData
    // if (currentData?.nodes) {
    //   if (currentData?.nodes.length !== data.nodes.length || currentData.edges.length !== data.edges.length) {
    //     data.nodes = currentData.nodes
    //     data.edges = currentData.edges
    //   }
    // }
    const existed = data.nodes.find(it => it.id === option.key)
    if (existed) {
      graph.getNodes().forEach((node: any) => {
        graph.clearItemStates(node)
      })
      graph.setItemState(option.key, 'actived', true)
      let item = graph.findById(option.key)
      graph.focusItem(item, true)
      return
    }
    mergeData({
      nodes: [{
        id: option.key,
        attrs: option.attrs,
        style: {
          label: {
            value: option.name,
            offset: [0, 10]
          }
        },
        iconPath: iconPathList[option.tag?.name],
        tagName: option.tag?.name
      }],
      key: option.key
    })
  }

  const onSelectChange = (v: any, t:any) => {
    setTagId(v)
    getNodeList(value, v)
  }

  const onDropdownVisibleChange = () => {
    setOptions([])
  }

  const onInputChange = (e: any) => {
    setValue(e.target.value)
    getNodeList(e.target.value, tagId)
  }
  const onSearchChange = (e:any,input:any) => {
    setIcon(input?.tag?.name)
}

  const prefixSelector = (
    <Select style={{width: 100}} className='search-panel-select' value={tagId}  onChange={onSelectChange}
            onDropdownVisibleChange={onDropdownVisibleChange}>
      <Select.Option value="">全部</Select.Option>
      {spaceTagList?.map((item:any) => <Select.Option key={item.id} value={item.name}>{item.name}</Select.Option>)}
    </Select>
  );

  return (
    <Input.Group size="large" className="searchPanelContainer" style={{height: visible ? 'auto' : 0}}>
      {prefixSelector}
      <AutoComplete
        dropdownMatchSelectWidth={true}
        notFoundContent={loading ? <div style={{textAlign: 'center', padding: 30}}><Spin/></div> :
        <Empty description='暂无数据' image={Empty.PRESENTED_IMAGE_SIMPLE}/>}
        backfill={false}
        options={options}
        value={value}
        onSelect={onSelect}
        onChange={onSearchChange}
        >
        <Input.Search
        prefix={<img style={{
          width:"15px",
          height:"15px",
          marginRight: "5px",
          display:iconPathList[icon]?"block":"none"}}
          src={iconPathList[icon]} alt="" />}
        size="large"
        onSearch={handleSearch}
        onChange={onInputChange}
        placeholder="请输入查找关键字"/>
      </AutoComplete>
    </Input.Group>

  )
}
