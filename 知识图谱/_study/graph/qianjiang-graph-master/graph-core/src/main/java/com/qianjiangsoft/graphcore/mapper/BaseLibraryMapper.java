package com.qianjiangsoft.graphcore.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.qianjiangsoft.graphcore.domain.entity.BaseLibrary;
import com.qianjiangsoft.graphcore.domain.param.DaoSelectBaseLibraryListWithCountParam;
import org.apache.ibatis.annotations.Mapper;

import java.util.List;

/**
 * 基础-分组信息Mapper接口
 * 
 * @author YoCiyy
 * @date 2021-04-07
 */
@Mapper
public interface BaseLibraryMapper extends BaseMapper<BaseLibrary> {

    List<BaseLibrary> selectWithGraphCountByParam(DaoSelectBaseLibraryListWithCountParam param);
}
