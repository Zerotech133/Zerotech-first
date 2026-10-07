import React, {forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useState} from 'react'
import {debounce} from "lodash"
import {Select, Spin} from 'antd'

type props = {
  fetchOptions: (key: string) => any,
  debounceTimeout?: number,
  [key: string]: any
}

const DebounceSelect: React.FC<props> = forwardRef(({fetchOptions, debounceTimeout = 300, ...props}, ref) => {
  const [fetching, setFetching] = useState(false)
  const [options, setOptions] = useState([])
  const [defaultValue, setDefaultValue] = useState<any[]>([])
  const debounceFetcher = useMemo(() => {
    const loadOptions = (value: string) => {
      setOptions([])
      setFetching(true)
      fetchOptions(value).then((newOptions: any) => {
        setOptions(newOptions)
        setFetching(false)
      }).catch((e: Error) => {
        setOptions([])
        setFetching(false)
        console.error(e)
      })
    }
    return debounce(loadOptions, debounceTimeout)
  }, [fetchOptions, debounceTimeout])

  useImperativeHandle(ref, () => ({
    init: (options: [], values: []) => {
      setOptions(options)
      if (values?.length) setDefaultValue(values)
    }
  }), [])

  const onChange = useCallback((value: any[]) => {
    setDefaultValue(value)
    props.onChange(value)
  }, [props])

  useEffect(() => {
    if (props.vertexlist) setDefaultValue(props.vertexlist)
  }, [props.vertexlist])

  return (
    <Select
      labelInValue
      filterOption={false}
      onSearch={debounceFetcher}
      notFoundContent={fetching ? <Spin size="small"/> : null}
      {...props}
      onChange={onChange}
      options={options}
      value={defaultValue}
    />
  )
})

DebounceSelect.displayName = 'DebounceSelect'
export {DebounceSelect}
