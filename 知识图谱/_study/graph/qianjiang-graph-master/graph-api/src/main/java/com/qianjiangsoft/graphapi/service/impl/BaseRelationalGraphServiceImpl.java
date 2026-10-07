package com.qianjiangsoft.graphapi.service.impl;

import cn.hutool.core.bean.BeanUtil;
import cn.hutool.core.date.DateUtil;
import cn.hutool.core.util.IdUtil;
import cn.hutool.core.util.StrUtil;
import com.baomidou.mybatisplus.core.conditions.query.LambdaQueryWrapper;
import com.github.pagehelper.PageInfo;
import com.qianjiangsoft.graphapi.service.BaseLibraryService;
import com.qianjiangsoft.graphapi.service.BaseRelationalGraphService;
import com.qianjiangsoft.graphcore.constants.ResultCode;
import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDataDTO;
import com.qianjiangsoft.graphcore.dto.BaseRelationalGraphDetailDTO;
import com.qianjiangsoft.graphcore.dto.SelectBaseRelationalGraphListDTO;
import com.qianjiangsoft.graphcore.engine.SpaceContextHolder;
import com.qianjiangsoft.graphcore.domain.entity.BaseRelationalGraph;
import com.qianjiangsoft.graphcore.exception.BusinessException;
import com.qianjiangsoft.graphcore.mapper.BaseRelationalGraphMapper;
import com.qianjiangsoft.graphcore.page.PageDataVO;
import com.qianjiangsoft.graphcore.page.PageUtil;
import com.qianjiangsoft.graphcore.param.AddBaseRelationalGraphParam;
import com.qianjiangsoft.graphcore.param.BaseLibraryParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphDataParam;
import com.qianjiangsoft.graphcore.param.UpdateBaseRelationalGraphParam;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;

import java.util.List;

/**
 * @author marshallwu
 * @date 2021/12/22 17:48
 */
@Service
public class BaseRelationalGraphServiceImpl implements BaseRelationalGraphService {

    @Autowired
    BaseRelationalGraphMapper baseRelationalGraphMapper;

    @Autowired
    BaseLibraryService baseLibraryService;

    @Override
    public PageDataVO<SelectBaseRelationalGraphListDTO> selectGraphPageByLibraryId(String libraryId, String keyword) {

        //查询列表
        LambdaQueryWrapper<BaseRelationalGraph> queryWrapper = new LambdaQueryWrapper<>();
        queryWrapper.eq(BaseRelationalGraph::getLibraryId, libraryId)
                .like(StrUtil.isNotEmpty(keyword), BaseRelationalGraph::getName, keyword)
                .orderByDesc(BaseRelationalGraph::getCreateTime);

        PageUtil.startPage();
        List<BaseRelationalGraph> graphList = baseRelationalGraphMapper.selectList(queryWrapper);
        return PageUtil.getPageDataVO(BeanUtil.copyToList(graphList, SelectBaseRelationalGraphListDTO.class),
                PageInfo.of(graphList).getTotal());
    }


    @Override
    public BaseRelationalGraphDetailDTO getGraphDetailById(String id) {
        BaseRelationalGraphDetailDTO result = BeanUtil.copyProperties(baseRelationalGraphMapper.selectById(id), BaseRelationalGraphDetailDTO.class);

        return result;
    }

    @Override
    public BaseRelationalGraphDataDTO getGraphDataById(String id) {
        return BeanUtil.copyProperties(baseRelationalGraphMapper.selectById(id), BaseRelationalGraphDataDTO.class);
    }

    @Override
    public String addGraph(AddBaseRelationalGraphParam param) {

        if(StrUtil.isNotEmpty(param.getLibraryId())) {
            //检查name重名
            LambdaQueryWrapper<BaseRelationalGraph> queryWrapper = new LambdaQueryWrapper<>();
            queryWrapper.eq(BaseRelationalGraph::getName, param.getName())
                    .eq(BaseRelationalGraph::getLibraryId, param.getLibraryId());
            if(baseRelationalGraphMapper.selectCount(queryWrapper) > 0){
                throw new BusinessException(ResultCode.BUSINESS_DUPLICATE_RECORD_ERROR, "已经存在同名图谱");
            }
        }else {
            //保存图谱库
            BaseLibraryParam baseLibraryParam = new BaseLibraryParam();
            baseLibraryParam.setName(param.getLibraryName());
            baseLibraryParam.setGroupId(param.getGroupId());
            baseLibraryService.insertBaseLibrary(baseLibraryParam);

            //回写library id
            param.setLibraryId(baseLibraryParam.getId());
        }

        //保存图谱数据
        BaseRelationalGraph baseRelationalGraph = BeanUtil.copyProperties(param, BaseRelationalGraph.class);
        baseRelationalGraph.setId(IdUtil.simpleUUID());
        baseRelationalGraph.setCreateTime(DateUtil.date());
        baseRelationalGraph.setSpaceId(SpaceContextHolder.getSpaceId());
        baseRelationalGraphMapper.insert(baseRelationalGraph);

        return baseRelationalGraph.getId();
    }


    @Override
    public void updateGraph(UpdateBaseRelationalGraphParam param) {

        //保存图谱数据
        BaseRelationalGraph baseRelationalGraph = BeanUtil.copyProperties(param, BaseRelationalGraph.class);
        baseRelationalGraphMapper.updateById(baseRelationalGraph);
    }

    @Override
    public void updateGraphData(UpdateBaseRelationalGraphDataParam param) {

        //保存图谱数据
        BaseRelationalGraph baseRelationalGraph = BeanUtil.copyProperties(param, BaseRelationalGraph.class);
        baseRelationalGraphMapper.updateById(baseRelationalGraph);
    }

    @Override
    public void deleteGraphById(String id) {

        //删除数据
        baseRelationalGraphMapper.deleteById(id);
    }
}
